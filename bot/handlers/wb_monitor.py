"""wb_monitor.py — thin facade: shared constants, imports, and core navigation handlers.

All domain-specific handlers have been extracted into separate modules:
- _shared.py      — FSM states, plan helpers, spinner utils
- similar_filter.py (service) — search/filter engine
- compare.py      — wbm:compare:* handlers
- quick_item.py   — wbm:quick:* handlers
- tracks.py       — wbm:list/page/pause/resume/remove handlers
- find_cheaper.py — wbm:cheap/cheapmode/reviews handlers
- payment.py      — plan, payment, referral handlers
- admin.py        — wbm:admin:* handlers
- settings.py     — wbm:settings/qty/stock/sizes handlers
- support.py      — wbm:help/support:* handlers
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.callbacks import NavAction, NavCb, TrackAction, TrackActionCb
from bot.db.models import TrackModel
from bot import text as tx
from bot.keyboards.inline import (
    add_item_prompt_kb,
)
from bot.services.repository import (
    count_user_tracks,
    create_track,
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_tracks,
    runtime_config_view,
)

from bot.services.wb_client import extract_wb_item_id, fetch_product

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

# Re-export shared helpers so cmds.py and other modules can still import from here
from bot.handlers._shared import (  # noqa: F401
    AddItemState,
    SettingsState,
    SupportState,
    _can_use_compare,
    _is_paid_plan,
    _track_kb_with_usage,
)
from bot.handlers.quick_item import (  # noqa: F401
    _quick_preview_text,
    _quick_item_kb_with_usage,
)
from bot.handlers._dashboard import build_dashboard_view

router = Router()
logger = logging.getLogger(__name__)

_LIKELY_WB_INPUT_RE = re.compile(r"wildberries|wb\.ru|\d{6,15}", re.IGNORECASE)


def _looks_like_wb_input(text: str) -> bool:
    return bool(_LIKELY_WB_INPUT_RE.search(text))


async def _add_item_direct_impl(
    *,
    msg: Message,
    session: "AsyncSession",
    redis: "Redis",
    url_or_text: str,
) -> bool:
    """Direct add flow. Returns True only when item was successfully added."""
    wb_item_id = extract_wb_item_id(url_or_text)
    if not wb_item_id:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return False

    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username, redis=redis
    )
    from sqlalchemy import select as _select
    from bot.db.models import TrackModel as _TM

    existing = await session.scalar(
        _select(_TM).where(
            _TM.user_id == user.id,
            _TM.wb_item_id == wb_item_id,
            _TM.is_deleted.is_(False),
        )
    )
    if existing:
        await msg.answer(tx.QUICK_ALREADY_TRACKED)
        return False

    from bot.handlers._shared import _track_limit

    track_count = await count_user_tracks(session, user.id, active_only=True)
    limit = _track_limit(user.plan)
    if track_count >= limit:
        await msg.answer(tx.TRACK_LIMIT_REACHED.format(limit=limit))
        return False

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer(tx.PRODUCT_FETCH_ERROR)
        return False

    cfg = runtime_config_view(await get_runtime_config(session))
    interval = (
        cfg.pro_interval_min if _is_paid_plan(user.plan) else cfg.free_interval_min
    )
    track_url = f"https://www.wildberries.ru/catalog/{wb_item_id}/detail.aspx"
    track = await create_track(
        session,
        user.id,
        wb_item_id,
        track_url,
        product.title,
        product.price,
        product.in_stock,
        product.total_qty,
        product.sizes,
        product.rating,
        product.reviews,
        interval,
    )
    await session.commit()

    tracks = await get_user_tracks(session, user.id)
    page = next((idx for idx, t in enumerate(tracks) if t.id == track.id), 0)

    from bot.keyboards.inline import format_track_text

    await msg.answer(
        "✅ Товар добавлен в список мониторинга!\n\n" + format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=msg.from_user.id,
            user_plan=user.plan,
            track=track,
            page=page,
            total=len(tracks),
        ),
    )
    return True


# ─── Core navigation ─────────────────────────────────────────────────────────


@router.callback_query(NavCb.filter(F.action == NavAction.HOME))
async def wb_home_cb(
    cb: CallbackQuery,
    callback_data: NavCb,
    session: "AsyncSession",
    redis: "Redis" = None,
) -> None:
    # redis arg is optional (home is called from support.py without it)
    _user, text, reply_markup = await build_dashboard_view(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
    )
    await cb.message.edit_text(
        text,
        reply_markup=reply_markup,
    )


@router.callback_query(NavCb.filter(F.action == NavAction.NOOP))
async def wb_noop_cb(cb: CallbackQuery, callback_data: NavCb) -> None:
    await cb.answer()


@router.callback_query(NavCb.filter(F.action == NavAction.ADD))
async def wb_add_cb(cb: CallbackQuery, callback_data: NavCb, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddItemState.waiting_for_url)
    await cb.message.edit_text(tx.ADD_ITEM_PROMPT, reply_markup=add_item_prompt_kb())


@router.callback_query(NavCb.filter(F.action == NavAction.CANCEL))
async def wb_cancel_cb(
    cb: CallbackQuery,
    callback_data: NavCb,
    session: "AsyncSession",
    state: FSMContext,
) -> None:
    await state.clear()
    _user, text, reply_markup = await build_dashboard_view(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
    )
    await cb.message.edit_text(
        text,
        reply_markup=reply_markup,
    )


# ─── Add item flow: direct add when user is in AddItemState ───────────────────


@router.message(AddItemState.waiting_for_url, F.text)
async def wb_add_item_direct(
    msg: Message, session: "AsyncSession", redis: "Redis", state: FSMContext
) -> None:
    """Обработчик в состоянии ожидания ссылки: сразу добавляет товар, минуя quick-превью."""
    url_or_text = msg.text.strip()
    if not _looks_like_wb_input(url_or_text):
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return

    added = await _add_item_direct_impl(
        msg=msg,
        session=session,
        redis=redis,
        url_or_text=url_or_text,
    )
    if not added:
        return

    await state.clear()


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.BACK))
async def wb_back_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    from bot.keyboards.inline import format_track_text
    from bot.services.repository import get_user_tracks

    track_id = callback_data.track_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                ),
            )
            break


# ─── Catch-all: text message → add WB item ──────────────────────────────────


@router.message(StateFilter(None), F.text)
async def wb_add_item_from_text(
    msg: Message,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    url_or_text = msg.text.strip()

    if not _looks_like_wb_input(url_or_text):
        return

    wb_item_id = extract_wb_item_id(url_or_text)
    if not wb_item_id:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return

    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username, redis=redis
    )
    existing = await session.scalar(
        select(TrackModel).where(
            TrackModel.user_id == user.id,
            TrackModel.wb_item_id == wb_item_id,
            TrackModel.is_deleted.is_(False),
        )
    )

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer(tx.PRODUCT_FETCH_ERROR)
        return

    await msg.answer(
        _quick_preview_text(product=product, already_tracked=bool(existing)),
        reply_markup=await _quick_item_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=msg.from_user.id,
            user_plan=user.plan,
            wb_item_id=wb_item_id,
            already_tracked=bool(existing),
        ),
    )
