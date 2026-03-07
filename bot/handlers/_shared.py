"""_shared.py — shared constants, FSM states and helper functions used across handlers."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, Message

from bot.db.redis import FeatureUsageDailyRD
from bot.db.models import TrackModel
from bot.keyboards.inline import paged_track_kb
from bot import text as tx

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

# ─── Plan constants ───────────────────────────────────────────────────────────
_PLAN_PRO_CODE = "pro"
_PLAN_PRO_PLUS_CODE = "proplus"
_PLAN_DB_PRO = "pro"
_PLAN_DB_PRO_PLUS = "pro_plus"
_PAID_PLANS = {_PLAN_DB_PRO, _PLAN_DB_PRO_PLUS}

_PLAN_BASE_AMOUNT = {_PLAN_PRO_CODE: 150, _PLAN_PRO_PLUS_CODE: 250}
_PLAN_DAYS = {_PLAN_PRO_CODE: 30, _PLAN_PRO_PLUS_CODE: 30}

_FEATURE_LIMITS: dict[str, dict[str, int]] = {
    "free": {"cheap": 2, "reviews": 3},
    _PLAN_DB_PRO: {"cheap": 300, "reviews": 180},
    _PLAN_DB_PRO_PLUS: {"cheap": 600, "reviews": 360},
}
_COMPARE_DAILY_LIMIT = 2


# ─── Plan helpers ─────────────────────────────────────────────────────────────

def _is_paid_plan(plan: str) -> bool:
    return plan in _PAID_PLANS


def _normalize_offer_code(raw: str | None) -> str:
    if raw == _PLAN_PRO_PLUS_CODE:
        return _PLAN_PRO_PLUS_CODE
    return _PLAN_PRO_CODE


def _plan_db_name_from_offer(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return _PLAN_DB_PRO_PLUS if normalized == _PLAN_PRO_PLUS_CODE else _PLAN_DB_PRO


def _plan_base_amount(offer_code: str) -> int:
    return _PLAN_BASE_AMOUNT[_normalize_offer_code(offer_code)]


def _plan_days(offer_code: str) -> int:
    return _PLAN_DAYS[_normalize_offer_code(offer_code)]


def _plan_title(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_TITLE
        if normalized == _PLAN_PRO_PLUS_CODE
        else tx.PLAN_OFFER_PRO_TITLE
    )


def _plan_note(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_NOTE
        if normalized == _PLAN_PRO_PLUS_CODE
        else tx.PLAN_OFFER_PRO_NOTE
    )


def _discounted_amount(base_amount: int, discount: object | None) -> int:
    if not discount:
        return base_amount
    percent = int(getattr(discount, "percent", 0) or 0)
    return max(1, int(round(base_amount * (100 - percent) / 100)))


def _feature_period(plan: str) -> str:
    return "month" if _is_paid_plan(plan) else "day"


def _feature_period_phrase(period: str) -> str:
    return "в месяц" if period == "month" else "в день"


def _feature_period_title(period: str) -> str:
    return "месяц" if period == "month" else "день"


def _feature_limit(plan: str, feature: str) -> int:
    plan_key = plan if plan in _FEATURE_LIMITS else "free"
    limits = _FEATURE_LIMITS.get(plan_key, _FEATURE_LIMITS["free"])
    return int(limits.get(feature, 0))


def _track_limit(plan: str) -> int:
    if plan == _PLAN_DB_PRO_PLUS:
        return 100
    if plan == _PLAN_DB_PRO:
        return 50
    return 5


def _can_use_compare(*, plan: str, admin: bool) -> bool:
    return admin or plan in _PAID_PLANS


def _has_active_subscription(user: object, *, now: datetime) -> bool:
    plan = str(getattr(user, "plan", "free"))
    if not _is_paid_plan(plan):
        return False
    expires_at = getattr(user, "pro_expires_at", None)
    if expires_at is None:
        return True
    return expires_at >= now


def _model_signature(model: str, review_limit: int) -> str:
    return f"{model}|limit:{review_limit}"


def _build_payment_payload(*, offer_code: str, days: int, amount: int, discount_activation_id: int | None) -> str:
    plan = _normalize_offer_code(offer_code)
    activation = discount_activation_id or 0
    return f"wbm_sub:{plan}:{days}:{activation}:{amount}"


def _parse_payment_payload(payload: str | None) -> tuple[int, int, str, int] | None:
    if not payload:
        return None
    parts = payload.split(":")
    if len(parts) == 3 and parts[0] == "wbm_pro_30d":
        try:
            activation_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            return None
        if activation_id < 0 or amount <= 0:
            return None
        return activation_id, amount, _PLAN_PRO_CODE, 30
    if len(parts) != 5 or parts[0] != "wbm_sub":
        return None
    offer_code = _normalize_offer_code(parts[1])
    try:
        days = int(parts[2])
        activation_id = int(parts[3])
        amount = int(parts[4])
    except ValueError:
        return None
    if activation_id < 0 or amount <= 0 or days <= 0:
        return None
    return activation_id, amount, offer_code, days


# ─── Plan offer text ──────────────────────────────────────────────────────────

def _plan_offer_text(*, offer_code: str, cfg: object, amount: int) -> str:
    plan_name = _plan_db_name_from_offer(offer_code)
    period = _feature_period(plan_name)
    return tx.PLAN_OFFER_TEXT.format(
        title=_plan_title(offer_code),
        days=_plan_days(offer_code),
        tracks_limit=_track_limit(plan_name),
        interval=cfg.pro_interval_min,
        cheap_period=_feature_period_phrase(period),
        reviews_period=_feature_period_phrase(period),
        cheap_limit=_feature_limit(plan_name, "cheap"),
        reviews_limit=_feature_limit(plan_name, "reviews"),
        card_amount=amount,
        stars_amount=amount,
        note=_plan_note(offer_code),
    )


# ─── FSM States ───────────────────────────────────────────────────────────────

class SettingsState(StatesGroup):
    waiting_for_targets = State()
    waiting_for_sizes = State()
    waiting_for_compare_items = State()
    waiting_for_pro_grant = State()
    waiting_for_free_interval = State()
    waiting_for_pro_interval = State()
    waiting_for_cheap_threshold = State()
    waiting_for_free_ai_limit = State()
    waiting_for_pro_ai_limit = State()
    waiting_for_review_sample_limit = State()
    waiting_for_analysis_model = State()
    waiting_for_promo_pro = State()
    waiting_for_promo_discount = State()


class SupportState(StatesGroup):
    waiting_for_message_or_media = State()
    waiting_for_media_confirmation = State()
    waiting_for_admin_reply = State()


# ─── Spinner ──────────────────────────────────────────────────────────────────

async def _progress_spinner(
    message: Message,
    *,
    base_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    hourglass_frames = ("⏳", "⌛️")
    dots_frames = (".", "..", "...")
    clean_base = base_text.rstrip(" .…")
    idx = 0
    while True:
        dots = dots_frames[idx % len(dots_frames)]
        hourglass = hourglass_frames[idx % len(hourglass_frames)]
        try:
            await message.edit_text(
                f"{clean_base}{dots} {hourglass}",
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        idx += 1
        await asyncio.sleep(1.1)


async def _stop_spinner(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


# ─── Track keyboard with usage ───────────────────────────────────────────────

async def _track_kb_with_usage(
    *,
    session: "AsyncSession",
    redis: "Redis",
    user_tg_id: int,
    user_plan: str,
    track: TrackModel,
    page: int,
    total: int,
    confirm_remove: bool = False,
) -> InlineKeyboardMarkup:
    cheap_limit = _feature_limit(user_plan, "cheap")
    reviews_limit = _feature_limit(user_plan, "reviews")
    period = _feature_period(user_plan)
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis, tg_user_id=user_tg_id, feature="cheap", period=period, session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis, tg_user_id=user_tg_id, feature="reviews", period=period, session=session,
    )
    return paged_track_kb(
        track, page, total,
        confirm_remove=confirm_remove,
        cheap_btn_text=tx.button_with_usage(tx.BTN_FIND_CHEAPER, used=cheap_used, limit=cheap_limit),
        reviews_btn_text=tx.button_with_usage(tx.BTN_REVIEW_ANALYSIS, used=reviews_used, limit=reviews_limit),
    )
