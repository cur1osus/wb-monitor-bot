from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import CommandStart

from bot.db.redis import MonitorUserRD
from bot.keyboards.inline import dashboard_kb, dashboard_text
from bot.services.repository import count_user_tracks, get_or_create_monitor_user
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from aiogram.types import Message
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def start_cmd(
    message: Message,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    if not message.from_user:
        return

    user = await get_or_create_monitor_user(
        session, message.from_user.id, message.from_user.username, redis=redis
    )
    await session.commit()

    # Прогрев Redis-кэша после создания/обновления
    await MonitorUserRD.from_model(user).save(redis)

    used = await count_user_tracks(session, user.id, active_only=True)
    admin = is_admin(message.from_user.id, se)

    await message.answer(
        text=dashboard_text(user.plan, used),
        reply_markup=dashboard_kb(admin),
    )
