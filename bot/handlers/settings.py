"""settings.py — wb_settings_* handlers."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import text as tx
from bot.keyboards.inline import format_track_text, settings_kb
from bot.services.repository import (
    get_or_create_monitor_user,
    get_user_track_by_id,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import SettingsState, _is_paid_plan

router = Router()
logger = logging.getLogger(__name__)


async def _hide_settings_prompt_keyboard(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")
    if not isinstance(prompt_message_id, int):
        return
    try:
        await msg.bot.edit_message_reply_markup(
            chat_id=msg.chat.id, message_id=prompt_message_id, reply_markup=None,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.regexp(r"wbm:settings:(\d+)"))
async def wb_settings_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
        reply_markup=settings_kb(
            track_id, has_sizes=bool(track.last_sizes),
            pro_plan=_is_paid_plan(user.plan),
            qty_on=track.watch_qty, stock_on=track.watch_stock,
            price_fluctuation_on=track.watch_price_fluctuation,
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:qty:(\d+)"))
async def wb_settings_qty_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    if not _is_paid_plan(user.plan):
        await cb.answer(tx.SETTINGS_QTY_PRO_ONLY, show_alert=True)
        return
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    track.watch_qty = not track.watch_qty
    await session.commit()
    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(track_id, has_sizes=bool(track.last_sizes),
                                      pro_plan=True, qty_on=track.watch_qty,
                                      stock_on=track.watch_stock,
                                      price_fluctuation_on=track.watch_price_fluctuation),
        )
    except TelegramBadRequest:
        pass
    await cb.answer(tx.SETTINGS_QTY_ANSWER.format(
        state=tx.SETTINGS_QTY_STATE_ON if track.watch_qty else tx.SETTINGS_QTY_STATE_OFF
    ))


@router.callback_query(F.data.regexp(r"wbm:stock:(\d+)"))
async def wb_settings_stock_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    track.watch_stock = not track.watch_stock
    await session.commit()
    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(track_id, has_sizes=bool(track.last_sizes),
                                      pro_plan=_is_paid_plan(user.plan), qty_on=track.watch_qty,
                                      stock_on=track.watch_stock,
                                      price_fluctuation_on=track.watch_price_fluctuation),
        )
    except TelegramBadRequest:
        pass
    await cb.answer(tx.SETTINGS_STOCK_ANSWER.format(
        state=tx.SETTINGS_STOCK_STATE_ON if track.watch_stock else tx.SETTINGS_STOCK_STATE_OFF
    ))


@router.callback_query(F.data.regexp(r"wbm:price_fluctuation:(\d+)"))
async def wb_settings_price_fluctuation_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    track.watch_price_fluctuation = not track.watch_price_fluctuation
    await session.commit()
    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(track_id, has_sizes=bool(track.last_sizes),
                                      pro_plan=_is_paid_plan(user.plan), qty_on=track.watch_qty,
                                      stock_on=track.watch_stock,
                                      price_fluctuation_on=track.watch_price_fluctuation),
        )
    except TelegramBadRequest:
        pass
    await cb.answer(tx.SETTINGS_PRICE_FLUCTUATION_ANSWER.format(
        state=(tx.SETTINGS_PRICE_FLUCTUATION_STATE_ON
               if track.watch_price_fluctuation
               else tx.SETTINGS_PRICE_FLUCTUATION_STATE_OFF)
    ))


def _sizes_picker_kb(track_id: int, all_sizes: list[str], selected: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, size in enumerate(all_sizes):
        mark = "✅" if size in selected else "☑️"
        row.append(InlineKeyboardButton(text=f"{mark} {size}",
                                         callback_data=f"wbm:sizesel:{track_id}:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=tx.BTN_SIZES_RESET, callback_data=f"wbm:sizes_clear:{track_id}")])
    rows.append([InlineKeyboardButton(text=tx.BTN_SIZES_APPLY, callback_data=f"wbm:sizes_apply:{track_id}")])
    rows.append([InlineKeyboardButton(text=tx.SETTINGS_CANCEL_BTN, callback_data=f"wbm:settings:{track_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _sizes_picker_text(selected: set[str]) -> str:
    selected_text = ", ".join(sorted(selected)) if selected else tx.SETTINGS_SIZES_NONE
    return f"{tx.SETTINGS_SIZES_PROMPT}\n\n{tx.SETTINGS_SIZES_SELECTED.format(sizes=selected_text)}"


@router.callback_query(F.data.regexp(r"wbm:sizes:(\d+)"))
async def wb_settings_sizes_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return
    selected = set(track.watch_sizes or track.last_sizes or [])
    await state.update_data(track_id=track_id, selected_sizes=list(selected))
    await state.set_state(SettingsState.waiting_for_sizes)
    await cb.message.edit_text(
        _sizes_picker_text(selected),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, selected),
    )


@router.callback_query(F.data.regexp(r"wbm:sizesel:(\d+):(\d+)"))
async def wb_settings_sizes_toggle_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    size_idx = int(parts[3])
    data = await state.get_data()
    track = await get_user_track_by_id(session, track_id)
    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return
    if size_idx < 0 or size_idx >= len(track.last_sizes):
        await cb.answer(tx.INVALID_PAGE, show_alert=True)
        return
    selected_raw = data.get("selected_sizes", None)
    selected = set(selected_raw) if selected_raw is not None else set(track.watch_sizes or track.last_sizes or [])
    size = track.last_sizes[size_idx]
    if size in selected:
        selected.remove(size)
    else:
        selected.add(size)
    await state.update_data(track_id=track_id, selected_sizes=list(selected))
    await cb.message.edit_text(
        _sizes_picker_text(selected),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, selected),
    )
    await cb.answer()


@router.message(SettingsState.waiting_for_sizes, F.text)
async def wb_settings_sizes_text_fallback(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    data = await state.get_data()
    track_id = data.get("track_id")
    if not track_id:
        await state.clear()
        return
    track = await get_user_track_by_id(session, int(track_id))
    if not track or not track.last_sizes:
        await state.clear()
        await msg.answer(tx.SETTINGS_NO_SIZES)
        return
    selected_raw = data.get("selected_sizes", None)
    selected = set(selected_raw) if selected_raw is not None else set(track.watch_sizes or track.last_sizes or [])
    await msg.answer(
        "ℹ️ Выбор размеров теперь только кнопками. Нажмите нужные размеры ниже и затем «✅ Подтвердить».",
        reply_markup=_sizes_picker_kb(track.id, track.last_sizes, selected),
    )


@router.callback_query(F.data.regexp(r"wbm:sizes_apply:(\d+)"))
async def wb_settings_sizes_apply_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    data = await state.get_data()
    track = await get_user_track_by_id(session, track_id)
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    if not track:
        await state.clear()
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    selected = set(data.get("selected_sizes") or [])
    track.watch_sizes = [s for s in (track.last_sizes or []) if s in selected]
    await session.commit()
    await state.clear()
    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
        reply_markup=settings_kb(
            track_id, has_sizes=bool(track.last_sizes), pro_plan=_is_paid_plan(user.plan),
            qty_on=track.watch_qty, stock_on=track.watch_stock,
            price_fluctuation_on=track.watch_price_fluctuation,
        ),
    )
    await cb.answer(tx.SETTINGS_SIZES_DONE.format(
        sizes=(", ".join(track.watch_sizes) if track.watch_sizes else tx.SETTINGS_SIZES_NONE)
    ))


@router.callback_query(F.data.regexp(r"wbm:sizes_clear:(\d+)"))
async def wb_settings_sizes_clear_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return
    track.watch_sizes = []
    await session.commit()
    await state.update_data(track_id=track_id, selected_sizes=[])
    await cb.message.edit_text(
        _sizes_picker_text(set()),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, set()),
    )
    await cb.answer(tx.SETTINGS_SIZES_RESET_DONE)
