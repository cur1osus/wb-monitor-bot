"""payment.py — plan, payment, referral handlers."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
)

from bot.db.redis import MonitorUserRD
from bot import text as tx
from bot.keyboards.inline import plan_offer_kb, plan_overview_kb, ref_kb
from bot.services.repository import (
    add_referral_reward_once,
    get_monitor_user_by_tg_id,
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_active_discount,
    mark_discount_activation_consumed,
    runtime_config_view,
    set_user_tracks_interval,
)
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import (
    _PLAN_PRO_CODE,
    _build_payment_payload,
    _discounted_amount,
    _has_active_subscription,
    _is_paid_plan,
    _normalize_offer_code,
    _parse_payment_payload,
    _plan_base_amount,
    _plan_days,
    _plan_db_name_from_offer,
    _plan_offer_text,
    _plan_title,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "wbm:plan:0")
async def wb_plan_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    from bot.db.redis import FeatureUsageDailyRD
    from bot.handlers._shared import (
        _feature_limit, _feature_period, _feature_period_phrase, _track_limit,
        _PLAN_DB_PRO, _PLAN_DB_PRO_PLUS,
    )
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    has_active_subscription = _has_active_subscription(user, now=now)
    cfg = runtime_config_view(await get_runtime_config(session))
    from bot.services.repository import count_user_tracks
    tracks_used = await count_user_tracks(session, user.id, active_only=True)
    tracks_limit = _track_limit(user.plan)
    interval = cfg.pro_interval_min if _is_paid_plan(user.plan) else cfg.free_interval_min
    cheap_period = _feature_period(user.plan)
    reviews_period = _feature_period(user.plan)
    cheap_limit = _feature_limit(user.plan, "cheap")
    reviews_limit = _feature_limit(user.plan, "reviews")
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis, tg_user_id=cb.from_user.id, feature="cheap", period=cheap_period, session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis, tg_user_id=cb.from_user.id, feature="reviews", period=reviews_period, session=session,
    )
    plan_label = (
        tx.PLAN_BADGE_PRO_PLUS if user.plan == _PLAN_DB_PRO_PLUS
        else tx.PLAN_BADGE_PRO if user.plan == _PLAN_DB_PRO
        else tx.PLAN_BADGE_FREE
    )
    text = tx.PLAN_TEXT.format(
        plan=plan_label, tracks_limit=tracks_limit, tracks_used=tracks_used, interval=interval,
        cheap_period=_feature_period_phrase(cheap_period), cheap_limit=cheap_limit,
        cheap_left=max(0, cheap_limit - cheap_used),
        reviews_period=_feature_period_phrase(reviews_period), reviews_limit=reviews_limit,
        reviews_left=max(0, reviews_limit - reviews_used),
    )
    if _is_paid_plan(user.plan) and user.pro_expires_at:
        text += tx.PLAN_EXPIRES_LINE.format(expires=user.pro_expires_at.strftime("%d.%m.%Y"))
    if not has_active_subscription:
        text += tx.PLAN_SELECT_PROMPT
    await cb.answer()
    await cb.message.edit_text(
        text,
        reply_markup=plan_overview_kb(show_purchase_buttons=not has_active_subscription),
    )


@router.callback_query(F.data.regexp(r"wbm:plan:offer:(pro|proplus)"))
async def wb_plan_offer_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    offer_code = _normalize_offer_code(cb.data.split(":")[3])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return
    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)
    cfg = runtime_config_view(await get_runtime_config(session))
    await cb.answer()
    await cb.message.edit_text(
        _plan_offer_text(offer_code=offer_code, cfg=cfg, amount=amount),
        reply_markup=plan_offer_kb(offer_code=offer_code, card_amount=amount,
                                    stars_amount=amount, discount=discount),
    )


@router.callback_query(F.data == "wbm:pay:choice")
async def wb_pay_choice_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    offer_code = _PLAN_PRO_CODE
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return
    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)
    cfg = runtime_config_view(await get_runtime_config(session))
    await cb.answer()
    await cb.message.edit_text(
        _plan_offer_text(offer_code=offer_code, cfg=cfg, amount=amount),
        reply_markup=plan_offer_kb(offer_code=offer_code, card_amount=amount,
                                    stars_amount=amount, discount=discount),
    )


@router.callback_query(F.data.regexp(r"wbm:pay:card(?::(pro|proplus))?$"))
async def wb_pay_card_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    from aiogram.types import LabeledPrice
    parts = cb.data.split(":")
    offer_code = _normalize_offer_code(parts[3] if len(parts) > 3 else None)
    if not se.provider_token:
        await cb.answer("❌ Оплата картой временно недоступна", show_alert=True)
        return
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return
    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    days = _plan_days(offer_code)
    amount_rub = _discounted_amount(_plan_base_amount(offer_code), discount)
    payload = _build_payment_payload(
        offer_code=offer_code, days=days, amount=amount_rub,
        discount_activation_id=(discount.activation_id if discount else None),
    )
    description = tx.PAYMENT_CARD_DESCRIPTION_BY_PLAN.format(
        plan=_plan_title(offer_code), days=days, amount=amount_rub,
    )
    label = f"{_plan_title(offer_code)} ({days} дн.)"
    await cb.message.answer_invoice(
        title=f"WB Monitor {_plan_title(offer_code)}", description=description,
        payload=payload, currency="RUB",
        prices=[LabeledPrice(label=label, amount=amount_rub * 100)],
        provider_token=se.provider_token,
    )


@router.callback_query(F.data.regexp(r"wbm:pay:stars(?::(pro|proplus))?$"))
async def wb_pay_stars_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    from aiogram.types import LabeledPrice
    parts = cb.data.split(":")
    offer_code = _normalize_offer_code(parts[3] if len(parts) > 3 else None)
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return
    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    days = _plan_days(offer_code)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)
    payload = _build_payment_payload(
        offer_code=offer_code, days=days, amount=amount,
        discount_activation_id=(discount.activation_id if discount else None),
    )
    label = f"{_plan_title(offer_code)} ({days} дн.)"
    if discount:
        label = tx.BTN_PAY_PRO_DISCOUNT.format(amount=amount, percent=discount.percent)
    await cb.message.answer_invoice(
        title=f"WB Monitor {_plan_title(offer_code)}",
        description=tx.PAYMENT_STARS_DESCRIPTION_BY_PLAN.format(
            plan=_plan_title(offer_code), days=days,
        ),
        payload=payload, currency="XTR",
        prices=[LabeledPrice(label=label, amount=amount)],
        provider_token="",
    )


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(msg: Message, session: "AsyncSession", redis: "Redis") -> None:
    payment = msg.successful_payment
    parsed_payload = _parse_payment_payload(payment.invoice_payload)
    paid_days = parsed_payload[3] if parsed_payload is not None else 30
    paid_offer_code = parsed_payload[2] if parsed_payload is not None else _PLAN_PRO_CODE
    paid_plan = _plan_db_name_from_offer(paid_offer_code)

    cfg = runtime_config_view(await get_runtime_config(session))
    user = await get_or_create_monitor_user(session, msg.from_user.id, msg.from_user.username)
    now = datetime.now(UTC).replace(tzinfo=None)
    base_expiry = (
        user.pro_expires_at if user.pro_expires_at and user.pro_expires_at > now else now
    )
    user.plan = paid_plan
    user.pro_expires_at = base_expiry + timedelta(days=paid_days)
    await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)

    if parsed_payload is not None:
        discount_activation_id, _amount, _offer_code, _days = parsed_payload
        if discount_activation_id > 0:
            await mark_discount_activation_consumed(session, activation_id=discount_activation_id, now=now)

    referral_bonus_applied = False
    if user.referred_by_tg_user_id and payment.telegram_payment_charge_id:
        referrer = await get_monitor_user_by_tg_id(session, user.referred_by_tg_user_id)
        if referrer:
            created = await add_referral_reward_once(
                session, referrer_user_id=referrer.id, invited_user_id=user.id,
                invited_tg_user_id=user.tg_user_id,
                payment_charge_id=payment.telegram_payment_charge_id, rewarded_days=7,
            )
            if created:
                ref_base = (
                    referrer.pro_expires_at
                    if referrer.pro_expires_at and referrer.pro_expires_at > now
                    else now
                )
                referrer.plan = "pro"
                referrer.pro_expires_at = ref_base + timedelta(days=7)
                await set_user_tracks_interval(session, referrer.id, cfg.pro_interval_min)
                referral_bonus_applied = True
                await MonitorUserRD.invalidate(redis, referrer.tg_user_id)
                try:
                    await msg.bot.send_message(referrer.tg_user_id, tx.REFERRAL_REWARD_NOTIFY)
                except Exception:
                    pass

    await session.commit()
    await MonitorUserRD.invalidate(redis, msg.from_user.id)
    text = tx.PRO_ACTIVATED_DAYS.format(days=paid_days)
    if referral_bonus_applied:
        text += tx.PRO_ACTIVATED_WITH_REFERRAL
    await msg.answer(text)


@router.callback_query(F.data == "wbm:ref:0")
async def wb_ref_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    await session.commit()
    bot_me = await cb.bot.me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user.referral_code}"
    await cb.message.edit_text(
        tx.REFERRAL_TEXT.format(ref_link=ref_link),
        reply_markup=ref_kb(ref_link),
    )
