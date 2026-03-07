"""tracks.py — handlers for track list, pagination, pause, resume, delete."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot import text as tx
from bot.keyboards.inline import dashboard_kb, dashboard_text, format_track_text
from bot.services.repository import (
    count_user_tracks,
    delete_track,
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_tracks,
    runtime_config_view,
    toggle_track_active,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import _can_use_compare, _track_kb_with_usage

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "wbm:list:0")
async def wb_list_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return
    track = tracks[0]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session, redis=redis, user_tg_id=cb.from_user.id,
            user_plan=user.plan, track=track, page=0, total=len(tracks),
        ),
    )


def _page_picker_kb(*, total: int, track_id: int, current_page: int, offset: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    per_row = 5
    max_buttons = 25
    safe_total = max(1, total)
    safe_offset = max(0, min(offset, max(0, safe_total - 1)))
    end = min(safe_total, safe_offset + max_buttons)

    page_buttons: list[InlineKeyboardButton] = []
    for i in range(safe_offset, end):
        label = f"[{i + 1}]" if i == current_page else str(i + 1)
        page_buttons.append(InlineKeyboardButton(text=label, callback_data=f"wbm:page:{i}"))
        if len(page_buttons) >= per_row:
            rows.append(page_buttons)
            page_buttons = []
    if page_buttons:
        rows.append(page_buttons)

    if safe_total > max_buttons:
        nav_row: list[InlineKeyboardButton] = []
        if safe_offset > 0:
            prev_offset = max(0, safe_offset - max_buttons)
            nav_row.append(InlineKeyboardButton(
                text="⬅️", callback_data=f"wbm:pagepick:{track_id}:{current_page}:{prev_offset}",
            ))
        nav_row.append(InlineKeyboardButton(
            text=f"{safe_offset + 1}-{end} / {safe_total}", callback_data="wbm:noop:0",
        ))
        if end < safe_total:
            nav_row.append(InlineKeyboardButton(
                text="➡️", callback_data=f"wbm:pagepick:{track_id}:{current_page}:{end}",
            ))
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(
        text=tx.BTN_BACK, callback_data=f"wbm:pagepickcancel:{track_id}:{current_page}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r"wbm:pagepick:(\d+):(\d+)(?::(\d+))?"))
async def wb_page_pick_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    current_page = int(parts[3])
    offset = int(parts[4]) if len(parts) > 4 else 0
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return
    if current_page < 0 or current_page >= len(tracks):
        current_page = 0
    if all(t.id != track_id for t in tracks):
        track_id = tracks[current_page].id
    await cb.answer()
    await cb.message.edit_reply_markup(
        reply_markup=_page_picker_kb(total=len(tracks), track_id=track_id,
                                      current_page=current_page, offset=offset)
    )


@router.callback_query(F.data.regexp(r"wbm:pagepickcancel:(\d+):(\d+)"))
async def wb_page_pick_cancel_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    current_page = int(parts[3])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return
    page = current_page
    if page < 0 or page >= len(tracks):
        page = 0
    track = tracks[page]
    if track.id != track_id:
        for idx, t in enumerate(tracks):
            if t.id == track_id:
                track = t
                page = idx
                break
    await cb.answer()
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session, redis=redis, user_tg_id=cb.from_user.id,
            user_plan=user.plan, track=track, page=page, total=len(tracks),
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:page:(\d+)"))
async def wb_page_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    page = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    if not tracks or page >= len(tracks):
        await cb.answer(tx.INVALID_PAGE, show_alert=True)
        return
    track = tracks[page]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session, redis=redis, user_tg_id=cb.from_user.id,
            user_plan=user.plan, track=track, page=page, total=len(tracks),
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:pause:(\d+)"))
async def wb_pause_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    await toggle_track_active(session, track_id, False)
    await session.commit()
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session, redis=redis, user_tg_id=cb.from_user.id,
                    user_plan=user.plan, track=track, page=idx, total=len(tracks),
                ),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:resume:(\d+)"))
async def wb_resume_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    await toggle_track_active(session, track_id, True)
    await session.commit()
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session, redis=redis, user_tg_id=cb.from_user.id,
                    user_plan=user.plan, track=track, page=idx, total=len(tracks),
                ),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:remove:(\d+)"))
async def wb_remove_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session, redis=redis, user_tg_id=cb.from_user.id,
                    user_plan=user.plan, track=track, page=idx, total=len(tracks),
                    confirm_remove=True,
                ),
            )
            await cb.answer(tx.REMOVE_CONFIRM)
            return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_no:(\d+)"))
async def wb_remove_no_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session, redis=redis, user_tg_id=cb.from_user.id,
                    user_plan=user.plan, track=track, page=idx, total=len(tracks),
                ),
            )
            await cb.answer(tx.REMOVE_CANCELLED)
            return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_yes:(\d+)"))
async def wb_remove_yes_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    tracks_before = await get_user_tracks(session, user.id)
    removed_index = next((idx for idx, t in enumerate(tracks_before) if t.id == track_id), 0)
    await delete_track(session, track_id)
    await session.commit()
    tracks_after = await get_user_tracks(session, user.id)
    if tracks_after:
        target_idx = min(removed_index, len(tracks_after) - 1)
        track = tracks_after[target_idx]
        await cb.message.edit_text(
            format_track_text(track),
            reply_markup=await _track_kb_with_usage(
                session=session, redis=redis, user_tg_id=cb.from_user.id,
                user_plan=user.plan, track=track, page=target_idx, total=len(tracks_after),
            ),
        )
        await cb.answer(tx.TRACK_DELETED)
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(user.plan, used, free_interval_min=cfg.free_interval_min,
                       pro_interval_min=cfg.pro_interval_min),
        reply_markup=dashboard_kb(admin, show_compare=_can_use_compare(plan=user.plan, admin=admin)),
    )
    await cb.answer(tx.TRACK_DELETED)
