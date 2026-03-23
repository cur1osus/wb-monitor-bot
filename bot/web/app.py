from __future__ import annotations

import os
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.web.auth import validate_init_data
from bot.web.a2ui_builder import build_dashboard_a2ui
from bot.db.models import MonitorUserModel, TrackModel
from bot.services.repository import get_user_tracks
from bot.settings import se

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSessionPool

app = FastAPI(title="WB Monitor Dashboard")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# We'll set these during startup in __main__.py
db_pool: "AsyncSessionPool" = None


def _wb_image_url(wb_item_id: int) -> str:
    """Generate WB CDN thumbnail URL for a product."""
    vol = wb_item_id // 100000
    part = wb_item_id // 1000
    # WB uses baskets 01-26; basket is derived from vol
    basket_num = _vol_to_basket(vol)
    return (
        f"https://basket-{basket_num:02d}.wbbasket.ru"
        f"/vol{vol}/part{part}/{wb_item_id}/images/c246x328/1.jpg"
    )


def _vol_to_basket(vol: int) -> int:
    """Map WB vol to basket server number (1-26)."""
    if vol <= 143:   return 1
    if vol <= 287:   return 2
    if vol <= 431:   return 3
    if vol <= 719:   return 4
    if vol <= 1007:  return 5
    if vol <= 1061:  return 6
    if vol <= 1115:  return 7
    if vol <= 1169:  return 8
    if vol <= 1313:  return 9
    if vol <= 1601:  return 10
    if vol <= 1655:  return 11
    if vol <= 1919:  return 12
    if vol <= 2045:  return 13
    if vol <= 2189:  return 14
    if vol <= 2405:  return 15
    if vol <= 2621:  return 16
    if vol <= 2837:  return 17
    if vol <= 3053:  return 18
    if vol <= 3269:  return 19
    if vol <= 3485:  return 20
    if vol <= 3701:  return 21
    if vol <= 3917:  return 22
    if vol <= 4133:  return 23
    if vol <= 4349:  return 24
    if vol <= 4565:  return 25
    return 26


async def _get_tg_user_id(x_telegram_init_data: str | None) -> int:
    """Validate init data and return tg_user_id, or raise HTTPException."""
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")

    user_data = validate_init_data(x_telegram_init_data, se.bot_token)
    if not user_data:
        raise HTTPException(status_code=403, detail="Invalid auth")

    try:
        return json.loads(user_data["user"])["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user data")


async def _get_user(session, tg_user_id: int) -> MonitorUserModel:
    user = await session.scalar(
        select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _serialize_track(t: TrackModel) -> dict:
    history = sorted(t.snapshots, key=lambda s: s.fetched_at)
    return {
        "id": t.id,
        "wb_item_id": t.wb_item_id,
        "title": t.title,
        "url": t.url,
        "image_url": _wb_image_url(t.wb_item_id),
        "price": float(t.last_price) if t.last_price else None,
        "rating": float(t.last_rating) if t.last_rating else None,
        "reviews": t.last_reviews,
        "in_stock": t.last_in_stock,
        "is_active": t.is_active,
        "watch_stock": t.watch_stock,
        "watch_price_fluctuation": t.watch_price_fluctuation,
        "watch_qty": t.watch_qty,
        "watch_sizes": t.watch_sizes or [],
        "last_sizes": t.last_sizes or [],
        "history": [
            {
                "date": s.fetched_at.isoformat(),
                "price": float(s.price_current) if s.price_current else None,
            }
            for s in history[-30:]
        ],
    }


@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/dashboard")
async def get_dashboard_api(x_telegram_init_data: str = Header(None)):
    tg_user_id = await _get_tg_user_id(x_telegram_init_data)
    async with db_pool() as session:
        user = await _get_user(session, tg_user_id)
        tracks = await get_user_tracks(session, user.id)
        return build_dashboard_a2ui(user, tracks)


@app.get("/api/tracks")
async def get_tracks_api(x_telegram_init_data: str = Header(None)):
    """JSON endpoint for Next.js dashboard."""
    tg_user_id = await _get_tg_user_id(x_telegram_init_data)

    async with db_pool() as session:
        user = await _get_user(session, tg_user_id)

        result = await session.execute(
            select(TrackModel)
            .options(selectinload(TrackModel.snapshots))
            .where(TrackModel.user_id == user.id, TrackModel.is_deleted.is_(False))
            .order_by(TrackModel.created_at.desc())
        )
        tracks = result.scalars().all()

        return {
            "user": {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "plan": user.plan,
            },
            "tracks": [_serialize_track(t) for t in tracks],
        }


@app.post("/api/action")
async def post_action_api(payload: dict, x_telegram_init_data: str = Header(None)):
    tg_user_id = await _get_tg_user_id(x_telegram_init_data)

    event = payload.get("event")
    component_id = payload.get("component_id")
    track_id = payload.get("track_id")

    async with db_pool() as session:
        user = await _get_user(session, tg_user_id)

        final_track_id = track_id or (
            int(component_id.split("_")[1])
            if component_id and "_" in component_id
            else None
        )

        if (event == "toggle_track" or event == "toggle") and final_track_id:
            track = await session.get(TrackModel, final_track_id)
            if track and track.user_id == user.id:
                track.is_active = not track.is_active
                await session.commit()

        return {"status": "ok"}


@app.delete("/api/tracks/{track_id}")
async def delete_track_api(track_id: int, x_telegram_init_data: str = Header(None)):
    """Soft-delete a track."""
    tg_user_id = await _get_tg_user_id(x_telegram_init_data)

    async with db_pool() as session:
        user = await _get_user(session, tg_user_id)
        track = await session.get(TrackModel, track_id)
        if not track or track.user_id != user.id:
            raise HTTPException(status_code=404, detail="Track not found")

        track.is_deleted = True
        track.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        track.is_active = False
        await session.commit()

    return {"status": "ok"}


@app.patch("/api/tracks/{track_id}/settings")
async def patch_track_settings(
    track_id: int,
    payload: dict,
    x_telegram_init_data: str = Header(None),
):
    """Update track notification settings."""
    tg_user_id = await _get_tg_user_id(x_telegram_init_data)

    async with db_pool() as session:
        user = await _get_user(session, tg_user_id)
        track = await session.get(TrackModel, track_id)
        if not track or track.user_id != user.id:
            raise HTTPException(status_code=404, detail="Track not found")

        is_pro = user.plan in ("pro", "pro_plus")

        if "watch_stock" in payload:
            track.watch_stock = bool(payload["watch_stock"])
        if "watch_price_fluctuation" in payload:
            track.watch_price_fluctuation = bool(payload["watch_price_fluctuation"])
        if "watch_qty" in payload:
            if not is_pro:
                raise HTTPException(status_code=403, detail="PRO plan required")
            track.watch_qty = bool(payload["watch_qty"])
        if "watch_sizes" in payload:
            sizes = payload["watch_sizes"]
            allowed = set(track.last_sizes or [])
            track.watch_sizes = [s for s in sizes if s in allowed]

        await session.commit()

        result = await session.execute(
            select(TrackModel)
            .options(selectinload(TrackModel.snapshots))
            .where(TrackModel.id == track_id)
        )
        updated = result.scalar_one()
        return _serialize_track(updated)
