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

from bot.db.models import TrackModel
from bot import text as tx
from bot.keyboards.inline import (
    add_item_prompt_kb,
    dashboard_kb,
    dashboard_text,
)
from bot.services.repository import (
    count_user_tracks,
    create_track,
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_tracks,
    runtime_config_view,
)

from bot.services.utils import is_admin
from bot.services.wb_client import extract_wb_item_id, fetch_product
from bot.settings import se

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

router = Router()
logger = logging.getLogger(__name__)

_LIKELY_WB_INPUT_RE = re.compile(r"wildberries|wb\.ru|\d{6,15}", re.IGNORECASE)
_ADD_MODE_KEY_PREFIX = "wbm:add-mode"


def _add_mode_key(tg_user_id: int) -> str:
    return f"{_ADD_MODE_KEY_PREFIX}:{tg_user_id}"


async def _add_item_direct_impl(
    *,
    msg: Message,
    session: "AsyncSession",
    redis: "Redis",
    url_or_text: str,
) -> bool:
    """Direct add flow (without quick preview). Returns True when input looked like WB intent."""
    if not _LIKELY_WB_INPUT_RE.search(url_or_text):
        return False

    wb_item_id = extract_wb_item_id(url_or_text)
    if not wb_item_id:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return True

    user = await get_or_create_monitor_user(session, msg.from_user.id, msg.from_user.username, redis=redis)
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
        return True

    from bot.handlers._shared import _track_limit, _is_paid_plan
    track_count = await count_user_tracks(session, user.id, active_only=True)
    limit = _track_limit(user.plan)
    if track_count >= limit:
        await msg.answer(tx.TRACK_LIMIT_REACHED.format(limit=limit))
        return True

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer(tx.PRODUCT_FETCH_ERROR)
        return True

    cfg = runtime_config_view(await get_runtime_config(session))
    interval = cfg.pro_interval_min if _is_paid_plan(user.plan) else cfg.free_interval_min
    track_url = f"https://www.wildberries.ru/catalog/{wb_item_id}/detail.aspx"
    track = await create_track(
        session, user.id, wb_item_id, track_url, product.title,
        product.price, product.in_stock, product.total_qty,
        product.sizes, product.rating, product.reviews, interval,
    )
    await session.commit()

    tracks = await get_user_tracks(session, user.id)
    page = next((idx for idx, t in enumerate(tracks) if t.id == track.id), 0)

    from bot.keyboards.inline import format_track_text
    await msg.answer(
        "✅ Товар добавлен в список мониторинга!\n\n" + format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session, redis=redis,
            user_tg_id=msg.from_user.id, user_plan=user.plan,
            track=track, page=page, total=len(tracks),
        ),
    )
    return True


# ─── Core navigation ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "wbm:home:0")
async def wb_home_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis" = None) -> None:
    # redis arg is optional (home is called from support.py without it)
    from bot.services.repository import count_user_tracks as _count
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    used = await _count(session, user.id, active_only=True)
    cfg = runtime_config_view(await get_runtime_config(session))
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(user.plan, used, free_interval_min=cfg.free_interval_min,
                       pro_interval_min=cfg.pro_interval_min),
        reply_markup=dashboard_kb(admin, show_compare=_can_use_compare(plan=user.plan, admin=admin)),
    )


@router.callback_query(F.data == "wbm:noop:0")
async def wb_noop_cb(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == "wbm:add:0")
async def wb_add_cb(cb: CallbackQuery, state: FSMContext, redis: "Redis") -> None:
    await state.set_state(AddItemState.waiting_for_url)
    await redis.set(_add_mode_key(cb.from_user.id), "1", ex=15 * 60)
    await cb.message.edit_text(tx.ADD_ITEM_PROMPT, reply_markup=add_item_prompt_kb())


@router.callback_query(F.data == "wbm:cancel:0")
async def wb_cancel_cb(cb: CallbackQuery, session: "AsyncSession", state: FSMContext, redis: "Redis") -> None:
    await state.clear()
    await redis.delete(_add_mode_key(cb.from_user.id))
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(user.plan, used, free_interval_min=cfg.free_interval_min,
                       pro_interval_min=cfg.pro_interval_min),
        reply_markup=dashboard_kb(admin, show_compare=_can_use_compare(plan=user.plan, admin=admin)),
    )


# ─── Add item flow: direct add when user is in AddItemState ───────────────────

@router.message(StateFilter(AddItemState.waiting_for_url), F.text)
async def wb_add_item_direct(msg: Message, session: "AsyncSession", redis: "Redis", state: FSMContext) -> None:
    """Обработчик в состоянии ожидания ссылки: сразу добавляет товар, минуя quick-превью."""
    await state.clear()
    await redis.delete(_add_mode_key(msg.from_user.id))
    handled = await _add_item_direct_impl(
        msg=msg,
        session=session,
        redis=redis,
        url_or_text=msg.text.strip(),
    )
    if not handled:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)


@router.callback_query(F.data.regexp(r"wbm:back:(\d+)"))
async def wb_back_cb(cb: CallbackQuery, session: "AsyncSession", redis: "Redis") -> None:
    from bot.keyboards.inline import format_track_text
    from bot.services.repository import get_user_tracks
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
            break


# ─── Catch-all: text message → add WB item ──────────────────────────────────

@router.message(StateFilter(None), F.text)
async def wb_add_item_from_text(msg: Message, session: "AsyncSession", redis: "Redis") -> None:
    url_or_text = msg.text.strip()

    # Fallback для кейсов, когда FSM-состояние могло не сохраниться/сброситься:
    # после кнопки «Добавить товар» форсим direct add в течение TTL.
    in_add_mode = await redis.get(_add_mode_key(msg.from_user.id))
    if in_add_mode:
        await redis.delete(_add_mode_key(msg.from_user.id))
        handled = await _add_item_direct_impl(
            msg=msg,
            session=session,
            redis=redis,
            url_or_text=url_or_text,
        )
        if not handled:
            await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return

    if not _LIKELY_WB_INPUT_RE.search(url_or_text):
        return

    wb_item_id = extract_wb_item_id(url_or_text)
    if not wb_item_id:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
        return

    user = await get_or_create_monitor_user(session, msg.from_user.id, msg.from_user.username, redis=redis)
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
            session=session, redis=redis, user_tg_id=msg.from_user.id,
            user_plan=user.plan, wb_item_id=wb_item_id, already_tracked=bool(existing),
        ),
    )
