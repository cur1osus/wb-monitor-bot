from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardMarkup

from bot.keyboards.inline import dashboard_kb, dashboard_text
from bot.services.repository import (
    count_user_tracks,
    get_or_create_monitor_user,
    get_runtime_config,
    runtime_config_view,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from bot.db.models import MonitorUserModel

from bot.handlers._shared import _can_use_compare


async def build_dashboard_view(
    *,
    session: "AsyncSession",
    tg_user_id: int,
    username: str | None,
) -> tuple["MonitorUserModel", str, InlineKeyboardMarkup]:
    user = await get_or_create_monitor_user(session, tg_user_id, username)
    used = await count_user_tracks(session, user.id, active_only=True)
    cfg = runtime_config_view(await get_runtime_config(session))
    admin = is_admin(tg_user_id, se)
    return (
        user,
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        dashboard_kb(admin, show_compare=_can_use_compare(plan=user.plan, admin=admin)),
    )
