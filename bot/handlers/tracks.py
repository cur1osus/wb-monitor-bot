"""tracks.py — handlers for track list, pagination, pause, resume, delete."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import text as tx
from bot.callbacks import NavAction, NavCb, TrackAction, TrackActionCb, TrackPageCb, TrackPagePickerCb
from bot.keyboards.inline import format_track_text, track_page_picker_kb
from bot.services.repository import (
    delete_track_for_user,
    get_or_create_monitor_user,
    get_user_tracks,
    toggle_track_active_for_user,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import _track_kb_with_usage
from bot.handlers._dashboard import build_dashboard_view

router = Router()
logger = logging.getLogger(__name__)


def _find_track_page(tracks: list[object], track_id: int) -> tuple[int, object] | None:
    for idx, track in enumerate(tracks):
        if getattr(track, "id", None) == track_id:
            return idx, track
    return None


async def _render_track_page(
    *,
    cb: CallbackQuery,
    session: "AsyncSession",
    redis: "Redis",
    user_plan: str,
    track: object,
    page: int,
    total: int,
    confirm_remove: bool = False,
) -> None:
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user_plan,
            track=track,
            page=page,
            total=total,
            confirm_remove=confirm_remove,
        ),
    )


@router.callback_query(NavCb.filter(F.action == NavAction.LIST))
async def wb_list_cb(
    cb: CallbackQuery,
    callback_data: NavCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return
    track = tracks[0]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            track=track,
            page=0,
            total=len(tracks),
        ),
    )


@router.callback_query(TrackPagePickerCb.filter(F.offset >= 0))
async def wb_page_pick_cb(
    cb: CallbackQuery,
    callback_data: TrackPagePickerCb,
    session: "AsyncSession",
) -> None:
    track_id = callback_data.track_id
    current_page = callback_data.current_page
    offset = callback_data.offset
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
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
        reply_markup=track_page_picker_kb(
            total=len(tracks),
            track_id=track_id,
            current_page=current_page,
            offset=offset,
        )
    )


@router.callback_query(TrackPagePickerCb.filter(F.offset == -1))
async def wb_page_pick_cancel_cb(
    cb: CallbackQuery,
    callback_data: TrackPagePickerCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    current_page = callback_data.current_page
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
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
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            track=track,
            page=page,
            total=len(tracks),
        ),
    )


@router.callback_query(TrackPageCb.filter())
async def wb_page_cb(
    cb: CallbackQuery,
    callback_data: TrackPageCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    page = callback_data.page
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks or page >= len(tracks):
        await cb.answer(tx.INVALID_PAGE, show_alert=True)
        return
    track = tracks[page]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            track=track,
            page=page,
            total=len(tracks),
        ),
    )


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.PAUSE))
async def wb_pause_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if not found:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    changed = await toggle_track_active_for_user(
        session,
        track_id=track_id,
        user_id=user.id,
        is_active=False,
    )
    if not changed:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await session.commit()
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if not found:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    idx, track = found
    await _render_track_page(
        cb=cb,
        session=session,
        redis=redis,
        user_plan=user.plan,
        track=track,
        page=idx,
        total=len(tracks),
    )


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.RESUME))
async def wb_resume_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if not found:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    changed = await toggle_track_active_for_user(
        session,
        track_id=track_id,
        user_id=user.id,
        is_active=True,
    )
    if not changed:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await session.commit()
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if not found:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    idx, track = found
    await _render_track_page(
        cb=cb,
        session=session,
        redis=redis,
        user_plan=user.plan,
        track=track,
        page=idx,
        total=len(tracks),
    )


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.REMOVE))
async def wb_remove_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if found:
        idx, track = found
        await _render_track_page(
            cb=cb,
            session=session,
            redis=redis,
            user_plan=user.plan,
            track=track,
            page=idx,
            total=len(tracks),
            confirm_remove=True,
        )
        await cb.answer(tx.REMOVE_CONFIRM)
        return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.REMOVE_NO))
async def wb_remove_no_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks, track_id)
    if found:
        idx, track = found
        await _render_track_page(
            cb=cb,
            session=session,
            redis=redis,
            user_plan=user.plan,
            track=track,
            page=idx,
            total=len(tracks),
        )
        await cb.answer(tx.REMOVE_CANCELLED)
        return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.REMOVE_YES))
async def wb_remove_yes_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks_before = await get_user_tracks(session, user.id)
    found = _find_track_page(tracks_before, track_id)
    if not found:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    removed_index, _track = found
    changed = await delete_track_for_user(session, track_id=track_id, user_id=user.id)
    if not changed:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await session.commit()
    tracks_after = await get_user_tracks(session, user.id)
    if tracks_after:
        target_idx = min(removed_index, len(tracks_after) - 1)
        track = tracks_after[target_idx]
        await _render_track_page(
            cb=cb,
            session=session,
            redis=redis,
            user_plan=user.plan,
            track=track,
            page=target_idx,
            total=len(tracks_after),
        )
        await cb.answer(tx.TRACK_DELETED)
        return

    _user, text, reply_markup = await build_dashboard_view(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
    )
    await cb.message.edit_text(text, reply_markup=reply_markup)
    await cb.answer(tx.TRACK_DELETED)
