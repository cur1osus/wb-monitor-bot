from __future__ import annotations

import os
import json
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

@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/dashboard")
async def get_dashboard_api(x_telegram_init_data: str = Header(None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    
    user_data = validate_init_data(x_telegram_init_data, se.bot_token)
    if not user_data:
        raise HTTPException(status_code=403, detail="Invalid auth")
    
    # parse user id from init data
    try:
        tg_user_id = json.loads(user_data["user"])["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user data")

    async with db_pool() as session:
        user = await session.scalar(
            select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        tracks = await get_user_tracks(session, user.id)
        return build_dashboard_a2ui(user, tracks)

@app.get("/api/tracks")
async def get_tracks_api(x_telegram_init_data: str = Header(None)):
    """Modern JSON endpoint for Next.js dashboard."""
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    
    user_data = validate_init_data(x_telegram_init_data, se.bot_token)
    if not user_data:
        raise HTTPException(status_code=403, detail="Invalid auth")
    
    try:
        tg_user_id = json.loads(user_data["user"])["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user data")

    async with db_pool() as session:
        user = await session.scalar(
            select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Fetch tracks with snapshots for charts
        result = await session.execute(
            select(TrackModel)
            .options(selectinload(TrackModel.snapshots))
            .where(TrackModel.user_id == user.id, TrackModel.is_deleted.is_(False))
            .order_by(TrackModel.created_at.desc())
        )
        tracks = result.scalars().all()
        
        data = []
        for t in tracks:
            # Sort snapshots by time
            history = sorted(t.snapshots, key=lambda s: s.fetched_at)
            
            data.append({
                "id": t.id,
                "wb_item_id": t.wb_item_id,
                "title": t.title,
                "url": t.url,
                "price": float(t.last_price) if t.last_price else None,
                "rating": float(t.last_rating) if t.last_rating else None,
                "in_stock": t.last_in_stock,
                "is_active": t.is_active,
                "history": [
                    {
                        "date": s.fetched_at.isoformat(),
                        "price": float(s.price_current) if s.price_current else None
                    }
                    for s in history[-30:] # Last 30 snapshots
                ]
            })
            
        return {
            "user": {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "plan": user.plan
            },
            "tracks": data
        }

@app.post("/api/action")
async def post_action_api(payload: dict, x_telegram_init_data: str = Header(None)):
    # Simple event handler
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
        
    user_data = validate_init_data(x_telegram_init_data, se.bot_token)
    if not user_data:
        raise HTTPException(status_code=403, detail="Invalid auth")
        
    try:
        tg_user_id = json.loads(user_data["user"])["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user data")

    event = payload.get("event")
    component_id = payload.get("component_id")
    track_id = payload.get("track_id")
    
    async with db_pool() as session:
        user = await session.scalar(
            select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Support both old A2UI style and new direct style
        final_track_id = track_id or (int(component_id.split("_")[1]) if component_id and "_" in component_id else None)

        if (event == "toggle_track" or event == "toggle") and final_track_id:
            track = await session.get(TrackModel, final_track_id)
            if track and track.user_id == user.id:
                track.is_active = not track.is_active
                await session.commit()
        
        return {"status": "ok"}
