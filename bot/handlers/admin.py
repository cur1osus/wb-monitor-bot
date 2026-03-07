"""admin.py — wb_admin_* handlers."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import text as tx
from bot.keyboards.inline import (
    admin_config_input_kb,
    admin_config_kb,
    admin_grant_pro_kb,
    admin_panel_kb,
    admin_promo_card_kb,
    admin_promo_input_kb,
    admin_promo_kb,
    admin_promo_list_kb,
)
from bot.db.redis import MonitorUserRD
from bot.services.repository import (
    apply_runtime_intervals,
    count_active_promos,
    count_promo_activations,
    create_promo_link,
    deactivate_promo_link,
    get_active_promos_page,
    get_admin_stats,
    get_monitor_user_by_tg_id,
    get_or_create_monitor_user,
    get_promo_by_id,
    get_runtime_config,
    runtime_config_view,
    set_user_tracks_interval,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession
    from bot.services.repository import AdminStats, RuntimeConfigView

from bot.handlers._shared import SettingsState

router = Router()
logger = logging.getLogger(__name__)

_ADMIN_PROMO_PAGE_SIZE = 8


def _admin_stats_text(stats: "AdminStats") -> str:
    return tx.admin_stats_text(stats)


def _admin_runtime_config_text(cfg: "RuntimeConfigView") -> str:
    return tx.admin_runtime_config_text(cfg)


def _promo_kind_text(kind: str) -> str:
    if kind == "pro_days":
        return tx.ADMIN_PROMO_KIND_PRO_DAYS
    if kind == "pro_discount":
        return tx.ADMIN_PROMO_KIND_DISCOUNT
    return tx.ADMIN_PROMO_KIND_UNKNOWN


def _promo_value_text(kind: str, value: int) -> str:
    if kind == "pro_days":
        return tx.ADMIN_PROMO_VALUE_DAYS.format(value=value)
    if kind == "pro_discount":
        return tx.ADMIN_PROMO_VALUE_PERCENT.format(value=value)
    return tx.ADMIN_PROMO_VALUE_RAW.format(value=value)


def _promo_list_item_text(promo: object) -> str:
    from html import escape as _e
    return tx.ADMIN_PROMO_LIST_ITEM.format(
        kind="🎁" if getattr(promo, "kind") == "pro_days" else "💸",
        value=_promo_value_text(getattr(promo, "kind"), int(getattr(promo, "value"))),
        expires=getattr(promo, "expires_at").strftime("%d.%m %H:%M"),
    )


async def _show_admin_promo_list(message: Message, *, session: "AsyncSession", page: int) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    total = await count_active_promos(session, now=now)
    if total <= 0:
        await message.edit_text(tx.ADMIN_PROMO_DEACTIVATE_EMPTY, reply_markup=admin_promo_kb())
        return
    total_pages = (total + _ADMIN_PROMO_PAGE_SIZE - 1) // _ADMIN_PROMO_PAGE_SIZE
    safe_page = min(max(0, page), total_pages - 1)
    promos = await get_active_promos_page(
        session, now=now, limit=_ADMIN_PROMO_PAGE_SIZE,
        offset=safe_page * _ADMIN_PROMO_PAGE_SIZE,
    )
    items = [(promo.id, _promo_list_item_text(promo)) for promo in promos]
    await message.edit_text(
        tx.ADMIN_PROMO_DEACTIVATE_LIST,
        reply_markup=admin_promo_list_kb(items, page=safe_page, total_pages=total_pages),
    )


def _promo_card_text(*, promo: object, activations: int, bot_username: str) -> str:
    from html import escape as _e
    expires_at = getattr(promo, "expires_at")
    now = datetime.now(UTC).replace(tzinfo=None)
    status = (
        tx.ADMIN_PROMO_STATUS_ACTIVE
        if expires_at >= now and getattr(promo, "is_active")
        else tx.ADMIN_PROMO_STATUS_EXPIRED
    )
    link = f"https://t.me/{bot_username}?start=promo_{getattr(promo, 'code')}"
    return tx.ADMIN_PROMO_CARD.format(
        kind=_promo_kind_text(getattr(promo, "kind")),
        value=_promo_value_text(getattr(promo, "kind"), int(getattr(promo, "value"))),
        status=status, activations=activations,
        created=getattr(promo, "created_at").strftime("%d.%m.%Y %H:%M"),
        expires=expires_at.strftime("%d.%m.%Y %H:%M"),
        link=_e(link),
    )


def _parse_promo_create_payload(text: str) -> tuple[int, int] | None:
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _parse_grant_pro_payload(text: str) -> tuple[int, int] | None:
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        tg_user_id = int(parts[0])
        days = int(parts[1])
    except ValueError:
        return None
    if tg_user_id <= 0 or not (1 <= days <= 365):
        return None
    return tg_user_id, days


@router.callback_query(F.data == "wbm:admin:0")
async def wb_admin_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    stats = await get_admin_stats(session, days=7)
    await cb.message.edit_text(_admin_stats_text(stats), reply_markup=admin_panel_kb(selected_days=7))


@router.callback_query(F.data == "wbm:admin:cfg")
async def wb_admin_cfg_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    cfg = runtime_config_view(await get_runtime_config(session))
    await cb.message.edit_text(_admin_runtime_config_text(cfg), reply_markup=admin_config_kb())


@router.callback_query(F.data == "wbm:admin:promo")
async def wb_admin_promo_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    await cb.message.edit_text(tx.ADMIN_PROMO_MENU_TEXT, reply_markup=admin_promo_kb())


@router.callback_query(F.data == "wbm:admin:promo:pro")
async def wb_admin_promo_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_promo_pro)
    await cb.message.edit_text(tx.ADMIN_PROMO_PRO_PROMPT, reply_markup=admin_promo_input_kb())


@router.callback_query(F.data == "wbm:admin:promo:discount")
async def wb_admin_promo_discount_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_promo_discount)
    await cb.message.edit_text(tx.ADMIN_PROMO_DISCOUNT_PROMPT, reply_markup=admin_promo_input_kb())


@router.callback_query(F.data == "wbm:admin:promo:deactivate")
async def wb_admin_promo_deactivate_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    await _show_admin_promo_list(cb.message, session=session, page=0)


@router.callback_query(F.data.regexp(r"wbm:admin:promo:list:(\d+)"))
async def wb_admin_promo_list_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    page = int(cb.data.split(":")[4])
    await _show_admin_promo_list(cb.message, session=session, page=page)


@router.callback_query(F.data.regexp(r"wbm:admin:promo:item:(\d+):(\d+)"))
async def wb_admin_promo_item_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    parts = cb.data.split(":")
    promo_id = int(parts[4])
    page = int(parts[5])
    promo = await get_promo_by_id(session, promo_id=promo_id)
    if promo is None or not promo.is_active:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_NOT_FOUND, show_alert=True)
        await _show_admin_promo_list(cb.message, session=session, page=page)
        return
    activations = await count_promo_activations(session, promo_id=promo.id)
    bot_me = await cb.bot.me()
    await cb.message.edit_text(
        _promo_card_text(promo=promo, activations=activations, bot_username=bot_me.username),
        reply_markup=admin_promo_card_kb(promo_id=promo.id, page=page),
    )


@router.callback_query(F.data.regexp(r"wbm:admin:promo:off:(\d+):(\d+)"))
async def wb_admin_promo_off_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    parts = cb.data.split(":")
    promo_id = int(parts[4])
    page = int(parts[5])
    promo = await get_promo_by_id(session, promo_id=promo_id)
    if promo is None or not promo.is_active:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_NOT_FOUND, show_alert=True)
        await _show_admin_promo_list(cb.message, session=session, page=page)
        return
    changed = await deactivate_promo_link(session, promo_id=promo.id)
    await session.commit()
    if changed:
        await cb.answer("✅ Промо ссылка деактивирована.")
    else:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_ALREADY, show_alert=True)
    await _show_admin_promo_list(cb.message, session=session, page=page)


@router.callback_query(F.data == "wbm:admin:cfg:free")
async def wb_admin_cfg_free_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_free_interval)
    await cb.message.edit_text(tx.ADMIN_FREE_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:pro")
async def wb_admin_cfg_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_pro_interval)
    await cb.message.edit_text(tx.ADMIN_PRO_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:cheap")
async def wb_admin_cfg_cheap_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_cheap_threshold)
    await cb.message.edit_text(tx.ADMIN_CHEAP_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:ai_free")
async def wb_admin_cfg_ai_free_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_free_ai_limit)
    await cb.message.edit_text(tx.ADMIN_FREE_AI_LIMIT_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:ai_pro")
async def wb_admin_cfg_ai_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_pro_ai_limit)
    await cb.message.edit_text(tx.ADMIN_PRO_AI_LIMIT_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:reviews_limit")
async def wb_admin_cfg_review_limit_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_review_sample_limit)
    await cb.message.edit_text(tx.ADMIN_REVIEW_SAMPLE_LIMIT_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:cfg:analysis_model")
async def wb_admin_cfg_analysis_model_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_analysis_model)
    await cb.message.edit_text(tx.ADMIN_ANALYSIS_MODEL_PROMPT, reply_markup=admin_config_input_kb())


@router.callback_query(F.data == "wbm:admin:grantpro")
async def wb_admin_grant_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_pro_grant)
    await cb.message.edit_text(tx.ADMIN_GRANT_PRO_PROMPT, reply_markup=admin_grant_pro_kb())


@router.callback_query(F.data.regexp(r"wbm:admin:stats:(\d+)"))
async def wb_admin_stats_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    days = int(cb.data.split(":")[3])
    if days not in {1, 7, 14, 30}:
        await cb.answer(tx.ADMIN_INVALID_PERIOD, show_alert=True)
        return
    stats = await get_admin_stats(session, days=days)
    try:
        await cb.message.edit_text(_admin_stats_text(stats), reply_markup=admin_panel_kb(selected_days=days))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await cb.answer()


# ── Message handlers for FSM states ──────────────────────────────────────────

@router.message(SettingsState.waiting_for_free_interval, F.text)
async def wb_admin_cfg_free_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_FREE_INT_ERROR); return
    if value < 5 or value > 1440:
        await msg.answer(tx.ADMIN_FREE_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.free_interval_min = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await apply_runtime_intervals(session, free_interval_min=cfg.free_interval_min,
                                   pro_interval_min=cfg.pro_interval_min)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_pro_interval, F.text)
async def wb_admin_cfg_pro_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_PRO_INT_ERROR); return
    if value < 1 or value > 1440:
        await msg.answer(tx.ADMIN_PRO_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.pro_interval_min = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await apply_runtime_intervals(session, free_interval_min=cfg.free_interval_min,
                                   pro_interval_min=cfg.pro_interval_min)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_cheap_threshold, F.text)
async def wb_admin_cfg_cheap_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_CHEAP_INT_ERROR); return
    if value < 10 or value > 95:
        await msg.answer(tx.ADMIN_CHEAP_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.cheap_match_percent = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_free_ai_limit, F.text)
async def wb_admin_cfg_free_ai_limit_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_FREE_AI_INT_ERROR); return
    if value < 1 or value > 50:
        await msg.answer(tx.ADMIN_FREE_AI_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.free_daily_ai_limit = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_pro_ai_limit, F.text)
async def wb_admin_cfg_pro_ai_limit_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_PRO_AI_INT_ERROR); return
    if value < 1 or value > 200:
        await msg.answer(tx.ADMIN_PRO_AI_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.pro_daily_ai_limit = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_review_sample_limit, F.text)
async def wb_admin_cfg_review_sample_limit_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_REVIEW_SAMPLE_LIMIT_INT_ERROR); return
    if value < 10 or value > 200:
        await msg.answer(tx.ADMIN_REVIEW_SAMPLE_LIMIT_RANGE_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.review_sample_limit_per_side = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_analysis_model, F.text)
async def wb_admin_cfg_analysis_model_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    model = msg.text.strip()
    if not model:
        await msg.answer(tx.ADMIN_MODEL_EMPTY_ERROR); return
    cfg = await get_runtime_config(session)
    cfg.analysis_model = model
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit(); await state.clear()
    await msg.answer(_admin_runtime_config_text(runtime_config_view(cfg)), reply_markup=admin_config_kb())


@router.message(SettingsState.waiting_for_promo_pro, F.text)
async def wb_admin_promo_pro_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    parsed = _parse_promo_create_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(tx.ADMIN_PROMO_PRO_FORMAT_ERROR, reply_markup=admin_promo_input_kb()); return
    days, life_hours = parsed
    if days < 1 or days > 365 or life_hours < 1 or life_hours > 720:
        await msg.answer(tx.ADMIN_PROMO_PRO_RANGE_ERROR, reply_markup=admin_promo_input_kb()); return
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=life_hours)
    promo = await create_promo_link(session, kind="pro_days", value=days, expires_at=expires_at,
                                     created_by_tg_user_id=msg.from_user.id)
    await session.commit(); await state.clear()
    bot_me = await msg.bot.me()
    link = f"https://t.me/{bot_me.username}?start=promo_{promo.code}"
    await msg.answer(tx.ADMIN_PROMO_CREATED_PRO.format(link=link, days=days,
                                                         expires=expires_at.strftime("%d.%m.%Y %H:%M")),
                     reply_markup=admin_promo_kb())


@router.message(SettingsState.waiting_for_promo_discount, F.text)
async def wb_admin_promo_discount_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    parsed = _parse_promo_create_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(tx.ADMIN_PROMO_DISCOUNT_FORMAT_ERROR, reply_markup=admin_promo_input_kb()); return
    discount_percent, life_hours = parsed
    if discount_percent < 1 or discount_percent > 90 or life_hours < 1 or life_hours > 720:
        await msg.answer(tx.ADMIN_PROMO_DISCOUNT_RANGE_ERROR, reply_markup=admin_promo_input_kb()); return
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=life_hours)
    promo = await create_promo_link(session, kind="pro_discount", value=discount_percent,
                                     expires_at=expires_at, created_by_tg_user_id=msg.from_user.id)
    await session.commit(); await state.clear()
    bot_me = await msg.bot.me()
    link = f"https://t.me/{bot_me.username}?start=promo_{promo.code}"
    await msg.answer(tx.ADMIN_PROMO_CREATED_DISCOUNT.format(link=link, percent=discount_percent,
                                                              expires=expires_at.strftime("%d.%m.%Y %H:%M")),
                     reply_markup=admin_promo_kb())


@router.message(SettingsState.waiting_for_pro_grant, F.text)
async def wb_admin_grant_pro_msg(msg: Message, state: FSMContext, session: "AsyncSession", redis: "Redis") -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear(); return
    parsed = _parse_grant_pro_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(tx.ADMIN_GRANT_PRO_FORMAT_ERROR, reply_markup=admin_grant_pro_kb()); return
    tg_user_id, days = parsed
    user = await get_monitor_user_by_tg_id(session, tg_user_id)
    if not user:
        await msg.answer(tx.ADMIN_GRANT_PRO_USER_NOT_FOUND, reply_markup=admin_grant_pro_kb()); return
    now = datetime.now(UTC).replace(tzinfo=None)
    base_expiry = user.pro_expires_at if user.pro_expires_at and user.pro_expires_at > now else now
    user.plan = "pro"
    user.pro_expires_at = base_expiry + timedelta(days=days)
    cfg = runtime_config_view(await get_runtime_config(session))
    await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)
    await session.commit()
    await MonitorUserRD.invalidate(redis, user.tg_user_id)
    await state.clear()
    stats = await get_admin_stats(session, days=7)
    await msg.answer(
        tx.ADMIN_GRANT_PRO_DONE.format(tg_user_id=user.tg_user_id, days=days,
                                        expires=user.pro_expires_at.strftime("%d.%m.%Y %H:%M"))
        + "\n\n" + _admin_stats_text(stats),
        reply_markup=admin_panel_kb(selected_days=7),
    )
    try:
        await msg.bot.send_message(
            user.tg_user_id,
            tx.ADMIN_GRANT_PRO_USER_NOTIFY.format(days=days,
                                                    expires=user.pro_expires_at.strftime("%d.%m.%Y %H:%M")),
        )
    except Exception:
        pass
