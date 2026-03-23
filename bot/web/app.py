from __future__ import annotations

import os
from typing import TYPE_CHECKING
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from bot.web.auth import validate_init_data
from bot.web.a2ui_builder import build_dashboard_a2ui
from bot.db.models import MonitorUserModel, TrackModel
from bot.services.repository import get_user_tracks
from bot.settings import se

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSessionPool

app = FastAPI(title="WB Monitor Dashboard")
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
    import json
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

@app.post("/api/action")
async def post_action_api(payload: dict, x_telegram_init_data: str = Header(None)):
    # Simple event handler for A2UI actions
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
        
    user_data = validate_init_data(x_telegram_init_data, se.bot_token)
    if not user_data:
        raise HTTPException(status_code=403, detail="Invalid auth")
        
    import json
    try:
        tg_user_id = json.loads(user_data["user"])["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user data")

    event = payload.get("event")
    component_id = payload.get("component_id")
    
    async with db_pool() as session:
        user = await session.scalar(
            select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if event == "toggle_track" and component_id.startswith("toggle_"):
            track_id = int(component_id.split("_")[1])
            track = await session.get(TrackModel, track_id)
            if track and track.user_id == user.id:
                track.is_active = not track.is_active
                await session.commit()
        
        # Always return fresh dashboard state
        tracks = await get_user_tracks(session, user.id)
        return build_dashboard_a2ui(user, tracks)
