"""_shared.py — shared constants, FSM states and helper functions used across handlers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, Message

from bot.enums import FeatureName, FeaturePeriod, PlanOfferCode, UserPlan
from bot.db.redis import FeatureUsageDailyRD
from bot.db.models import TrackModel
from bot.keyboards.inline import paged_track_kb
from bot import text as tx

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

# ─── Plan constants ───────────────────────────────────────────────────────────
_PLAN_PRO_CODE = PlanOfferCode.PRO.value
_PLAN_PRO_PLUS_CODE = PlanOfferCode.PRO_PLUS.value
_PLAN_DB_PRO = UserPlan.PRO.value
_PLAN_DB_PRO_PLUS = UserPlan.PRO_PLUS.value
_PAID_PLANS = {UserPlan.PRO, UserPlan.PRO_PLUS}

_PLAN_BASE_AMOUNT = {PlanOfferCode.PRO: 150, PlanOfferCode.PRO_PLUS: 250}
_PLAN_DAYS = {PlanOfferCode.PRO: 30, PlanOfferCode.PRO_PLUS: 30}

_FEATURE_LIMITS: dict[UserPlan, dict[FeatureName, int]] = {
    UserPlan.FREE: {FeatureName.CHEAP: 2, FeatureName.REVIEWS: 3},
    UserPlan.PRO: {FeatureName.CHEAP: 300, FeatureName.REVIEWS: 180},
    UserPlan.PRO_PLUS: {FeatureName.CHEAP: 600, FeatureName.REVIEWS: 360},
}
_COMPARE_DAILY_LIMIT = 2


# ─── Plan helpers ─────────────────────────────────────────────────────────────


def _normalize_user_plan(raw: UserPlan | str | None) -> UserPlan:
    if isinstance(raw, UserPlan):
        return raw
    try:
        return UserPlan((raw or UserPlan.FREE.value).strip().lower())
    except ValueError:
        return UserPlan.FREE


def _is_paid_plan(plan: UserPlan | str) -> bool:
    return _normalize_user_plan(plan) in _PAID_PLANS


def _normalize_offer_code(raw: PlanOfferCode | str | None) -> PlanOfferCode:
    if raw == PlanOfferCode.PRO_PLUS or raw == _PLAN_PRO_PLUS_CODE:
        return PlanOfferCode.PRO_PLUS
    return PlanOfferCode.PRO


def _plan_db_name_from_offer(offer_code: PlanOfferCode | str) -> UserPlan:
    normalized = _normalize_offer_code(offer_code)
    return UserPlan.PRO_PLUS if normalized == PlanOfferCode.PRO_PLUS else UserPlan.PRO


def _plan_base_amount(offer_code: PlanOfferCode | str) -> int:
    return _PLAN_BASE_AMOUNT[_normalize_offer_code(offer_code)]


def _plan_days(offer_code: PlanOfferCode | str) -> int:
    return _PLAN_DAYS[_normalize_offer_code(offer_code)]


def _plan_title(offer_code: PlanOfferCode | str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_TITLE
        if normalized == PlanOfferCode.PRO_PLUS
        else tx.PLAN_OFFER_PRO_TITLE
    )


def _plan_note(offer_code: PlanOfferCode | str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_NOTE
        if normalized == PlanOfferCode.PRO_PLUS
        else tx.PLAN_OFFER_PRO_NOTE
    )


def _discounted_amount(base_amount: int, discount: object | None) -> int:
    if not discount:
        return base_amount
    percent = int(getattr(discount, "percent", 0) or 0)
    return max(1, int(round(base_amount * (100 - percent) / 100)))


def _feature_period(plan: UserPlan | str) -> FeaturePeriod:
    return FeaturePeriod.MONTH if _is_paid_plan(plan) else FeaturePeriod.DAY


def _feature_period_phrase(period: FeaturePeriod | str) -> str:
    normalized = period if isinstance(period, FeaturePeriod) else FeaturePeriod(period)
    return "в месяц" if normalized == FeaturePeriod.MONTH else "в день"


def _feature_period_title(period: FeaturePeriod | str) -> str:
    normalized = period if isinstance(period, FeaturePeriod) else FeaturePeriod(period)
    return "месяц" if normalized == FeaturePeriod.MONTH else "день"


def _normalize_feature_name(raw: FeatureName | str) -> FeatureName:
    return (
        raw if isinstance(raw, FeatureName) else FeatureName(str(raw).strip().lower())
    )


def _feature_limit(plan: UserPlan | str, feature: FeatureName | str) -> int:
    plan_key = _normalize_user_plan(plan)
    limits = _FEATURE_LIMITS.get(plan_key, _FEATURE_LIMITS[UserPlan.FREE])
    return int(limits.get(_normalize_feature_name(feature), 0))


def _track_limit(plan: UserPlan | str) -> int:
    normalized = _normalize_user_plan(plan)
    if normalized == UserPlan.PRO_PLUS:
        return 100
    if normalized == UserPlan.PRO:
        return 50
    return 5


def _can_use_compare(*, plan: UserPlan | str, admin: bool) -> bool:
    return admin or _normalize_user_plan(plan) in _PAID_PLANS


def _has_active_subscription(user: object, *, now: datetime) -> bool:
    plan = _normalize_user_plan(getattr(user, "plan", UserPlan.FREE.value))
    if not _is_paid_plan(plan):
        return False
    expires_at = getattr(user, "pro_expires_at", None)
    if expires_at is None:
        return True
    return expires_at >= now


def _model_signature(model: str, review_limit: int) -> str:
    return f"{model}|limit:{review_limit}"


@dataclass(slots=True)
class FeatureUsageSnapshot:
    period: FeaturePeriod
    cheap_limit: int
    cheap_used: int
    reviews_limit: int
    reviews_used: int


async def _feature_usage_snapshot(
    *,
    session: "AsyncSession",
    redis: "Redis",
    user_tg_id: int,
    user_plan: UserPlan | str,
) -> FeatureUsageSnapshot:
    period = _feature_period(user_plan)
    cheap_limit = _feature_limit(user_plan, FeatureName.CHEAP)
    reviews_limit = _feature_limit(user_plan, FeatureName.REVIEWS)
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature=FeatureName.CHEAP,
        period=period,
        session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature=FeatureName.REVIEWS,
        period=period,
        session=session,
    )
    return FeatureUsageSnapshot(
        period=period,
        cheap_limit=cheap_limit,
        cheap_used=cheap_used,
        reviews_limit=reviews_limit,
        reviews_used=reviews_used,
    )


def _build_payment_payload(
    *,
    offer_code: PlanOfferCode | str,
    days: int,
    amount: int,
    discount_activation_id: int | None,
) -> str:
    plan = _normalize_offer_code(offer_code)
    activation = discount_activation_id or 0
    return f"wbm_sub:{plan.value}:{days}:{activation}:{amount}"


def _parse_payment_payload(
    payload: str | None,
) -> tuple[int, int, PlanOfferCode, int] | None:
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
        return activation_id, amount, PlanOfferCode.PRO, 30
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


def _plan_offer_text(
    *, offer_code: PlanOfferCode | str, cfg: object, amount: int
) -> str:
    plan_name = _plan_db_name_from_offer(offer_code)
    period = _feature_period(plan_name)
    return tx.PLAN_OFFER_TEXT.format(
        title=_plan_title(offer_code),
        days=_plan_days(offer_code),
        tracks_limit=_track_limit(plan_name),
        interval=cfg.pro_interval_min,
        cheap_period=_feature_period_phrase(period),
        reviews_period=_feature_period_phrase(period),
        cheap_limit=_feature_limit(plan_name, FeatureName.CHEAP),
        reviews_limit=_feature_limit(plan_name, FeatureName.REVIEWS),
        card_amount=amount,
        stars_amount=amount,
        note=_plan_note(offer_code),
    )


# ─── FSM States ───────────────────────────────────────────────────────────────


class AddItemState(StatesGroup):
    waiting_for_url = State()


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
    usage = await _feature_usage_snapshot(
        session=session,
        redis=redis,
        user_tg_id=user_tg_id,
        user_plan=user_plan,
    )
    return paged_track_kb(
        track,
        page,
        total,
        confirm_remove=confirm_remove,
        cheap_btn_text=tx.button_with_usage(
            tx.BTN_FIND_CHEAPER,
            used=usage.cheap_used,
            limit=usage.cheap_limit,
        ),
        reviews_btn_text=tx.button_with_usage(
            tx.BTN_REVIEW_ANALYSIS,
            used=usage.reviews_used,
            limit=usage.reviews_limit,
        ),
    )
