from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import CommandStart

from bot.db.redis import MonitorUserRD
from bot.keyboards.inline import dashboard_kb, dashboard_text
from bot import text as tx
from bot.services.repository import (
    create_promo_activation,
    bind_user_referrer_by_code,
    count_user_tracks,
    get_promo_activation,
    get_promo_by_code,
    get_or_create_monitor_user,
    get_runtime_config,
    runtime_config_view,
    set_user_tracks_interval,
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
_PROMO_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")


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


def _extract_promo_code(message_text: str | None) -> str | None:
    if not message_text:
        return None

    parts = message_text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None

    payload = parts[1].strip()
    if not payload.startswith("promo_"):
        return None

    code = payload[6:]
    if not _PROMO_CODE_RE.fullmatch(code):
        return None
    return code


@router.message(CommandStart())
async def start_cmd(
    message: Message,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    if not message.from_user:
        return

    ref_code = _extract_ref_code(message.text)
    promo_code = _extract_promo_code(message.text)

    user = await get_or_create_monitor_user(
        session, message.from_user.id, message.from_user.username, redis=redis
    )
    referred = False
    if ref_code:
        referred = await bind_user_referrer_by_code(
            session, user, ref_code, redis=redis
        )

    promo_feedback: str | None = None
    if promo_code:
        now = datetime.now(UTC).replace(tzinfo=None)
        promo = await get_promo_by_code(session, code=promo_code, now=now)
        if promo is None:
            promo_feedback = tx.PROMO_INVALID_OR_EXPIRED
        else:
            existing_activation = await get_promo_activation(
                session,
                promo_id=promo.id,
                user_id=user.id,
            )
            if existing_activation is not None:
                promo_feedback = tx.PROMO_ALREADY_USED
            elif promo.kind == "pro_days":
                cfg = runtime_config_view(await get_runtime_config(session))
                base_expiry = (
                    user.pro_expires_at
                    if user.pro_expires_at and user.pro_expires_at > now
                    else now
                )
                user.plan = "pro"
                user.pro_expires_at = base_expiry + timedelta(days=promo.value)
                await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)
                await create_promo_activation(
                    session,
                    promo_id=promo.id,
                    user_id=user.id,
                    tg_user_id=user.tg_user_id,
                    value_applied=promo.value,
                )
                await MonitorUserRD.invalidate(redis, user.tg_user_id)
                promo_feedback = tx.PROMO_PRO_APPLIED.format(days=promo.value)
            elif promo.kind == "pro_discount":
                await create_promo_activation(
                    session,
                    promo_id=promo.id,
                    user_id=user.id,
                    tg_user_id=user.tg_user_id,
                    value_applied=promo.value,
                )
                promo_feedback = tx.PROMO_DISCOUNT_APPLIED.format(percent=promo.value)
            else:
                promo_feedback = tx.PROMO_INVALID_OR_EXPIRED

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
    if promo_feedback:
        await message.answer(promo_feedback)
