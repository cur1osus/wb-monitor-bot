from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import CommandStart

from bot.db.redis import MonitorUserRD
from bot.keyboards.inline import dashboard_kb, dashboard_text
from bot import text as tx
from bot.services.repository import (
    bind_user_referrer_by_code,
    count_user_tracks,
    get_or_create_monitor_user,
    get_runtime_config,
    runtime_config_view,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from aiogram.types import Message
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

router = Router()
logger = logging.getLogger(__name__)
_REF_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")


def _extract_ref_code(message_text: str | None) -> str | None:
    if not message_text:
        return None

    parts = message_text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None

    payload = parts[1].strip()
    if payload.startswith("ref_"):
        payload = payload[4:]

    if not _REF_CODE_RE.fullmatch(payload):
        return None
    return payload.upper()


@router.message(CommandStart())
async def start_cmd(
    message: Message,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    if not message.from_user:
        return

    ref_code = _extract_ref_code(message.text)

    user = await get_or_create_monitor_user(
        session, message.from_user.id, message.from_user.username, redis=redis
    )
    referred = False
    if ref_code:
        referred = await bind_user_referrer_by_code(
            session, user, ref_code, redis=redis
        )

    await session.commit()

    # Прогрев Redis-кэша после создания/обновления
    await MonitorUserRD.from_model(user).save(redis)

    used = await count_user_tracks(session, user.id, active_only=True)
    cfg = runtime_config_view(await get_runtime_config(session))
    admin = is_admin(message.from_user.id, se)

    await message.answer(
        text=dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(admin),
    )

    if referred:
        await message.answer(tx.START_REF_LINKED)
