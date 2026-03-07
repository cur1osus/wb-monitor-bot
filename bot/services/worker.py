"""worker.py — adaptive WB price monitor worker."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from aiogram.types import LinkPreviewOptions
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import SnapshotModel, TrackModel
from bot.db.redis import WorkerStateRD
from bot import text as tx
from bot.services.repository import (
    due_tracks_python_safe,
    expire_pro_users,
    get_runtime_config,
    log_event,
)
from bot.services.wb_client import fetch_product, fetch_products_batch

if TYPE_CHECKING:
    from aiogram import Bot
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

ERROR_LIMIT = 5

# ─── Ночная пауза (МСК = UTC+3) ───────────────────────────────────────────────
_NIGHT_START_UTC_HOUR = 22   # 01:00 МСК
_NIGHT_END_UTC_HOUR = 4      # 07:00 МСК


def _is_night(now_utc: datetime) -> bool:
    """True if current UTC time falls in WB's low-activity night window (01-07 МСК)."""
    h = now_utc.hour
    # Диапазон [22, 4) в UTC
    return h >= _NIGHT_START_UTC_HOUR or h < _NIGHT_END_UTC_HOUR


def _should_skip_night(track: TrackModel) -> bool:
    """Return True if this track should be skipped during night hours.

    Exception: tracks watching stock for an out-of-stock item — they need
    to detect restock ASAP even at night.
    """
    if track.watch_stock and track.last_in_stock is False:
        return False  # always check: waiting for in-stock event
    return True


# ─── Адаптивный интервал ──────────────────────────────────────────────────────

def _adaptive_interval(track: TrackModel, base_min: int) -> int:
    """Calculate adaptive check_interval_min based on price change history.

    Coefficients:
        ≥ 7 changes → ×0.5  (very active — check 2× more often)
        3-6 changes  → ×0.75 (moderately active)
        0-2 changes  → ×1.5  (stable price)
        no change in 14 days → ×2.0 (very stable, apply on top of above rule)

    Result is clamped to [1, base_min * 3].
    """
    count = track.price_change_count or 0

    if count >= 7:
        coeff = 0.5
    elif count >= 3:
        coeff = 0.75
    else:
        coeff = 1.5

    # Extra multiplier for long stable items
    if track.last_price_changed_at is not None:
        days_since_change = (datetime.now(UTC).replace(tzinfo=None) - track.last_price_changed_at).days
        if days_since_change >= 14:
            coeff = min(coeff * 1.5, 2.0)
    elif track.price_change_count == 0 and track.created_at is not None:
        days_since_created = (datetime.now(UTC).replace(tzinfo=None) - track.created_at).days
        if days_since_created >= 14:
            coeff = min(coeff * 1.5, 2.0)

    result = int(base_min * coeff)
    return max(1, min(result, base_min * 3))


# ─── Приоритет очереди ────────────────────────────────────────────────────────

def _track_priority(track: TrackModel) -> tuple[int, int]:
    """Lower number = higher priority.

    0 — out-of-stock + watching stock (most urgent)
    1 — pro/pro_plus user
    2 — free user, active (many price changes)
    3 — free user, stable
    """
    plan = track.user.plan if track.user else "free"
    is_paid = plan in {"pro", "pro_plus"}
    out_of_stock_watch = track.watch_stock and track.last_in_stock is False

    if out_of_stock_watch:
        return (0, 0)
    if is_paid:
        return (1, -(track.price_change_count or 0))
    if (track.price_change_count or 0) >= 3:
        return (2, -(track.price_change_count or 0))
    return (3, 0)


# ─── Вспомогательные ──────────────────────────────────────────────────────────

def _msg(key: str, **kw: str | int) -> str:
    return tx.WORKER_EVENTS[key].format(**kw)


def _hash_event(track_id: int, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{track_id}:{kind}:{payload}".encode()).hexdigest()[:48]


def _base_interval_for_track(track: TrackModel, cfg_free: int, cfg_pro: int) -> int:
    plan = track.user.plan if track.user else "free"
    return cfg_pro if plan in {"pro", "pro_plus"} else cfg_free


# ─── Основной цикл ────────────────────────────────────────────────────────────

async def run_cycle(
    db_pool: async_sessionmaker[AsyncSession],
    redis: "Redis",
    bot: "Bot",
    session: ClientSession,
) -> None:
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    night = _is_night(now)

    async with db_pool() as db_session:
        cfg = await get_runtime_config(db_session)
        cfg_free = cfg.free_interval_min
        cfg_pro = cfg.pro_interval_min

        tracks = await due_tracks_python_safe(db_session, now_naive)

        # Применяем ночной фильтр и сортируем по приоритету
        if night:
            tracks = [t for t in tracks if not _should_skip_night(t)]
            if tracks:
                logger.debug("NIGHT_MODE: %d tracks remain (out-of-stock watchers)", len(tracks))

        tracks.sort(key=_track_priority)

        # ── Батч-запрос ──────────────────────────────────────────────────────
        wb_ids = [t.wb_item_id for t in tracks]
        batch_results: dict[int, object] = {}
        if wb_ids:
            try:
                batch_results = await fetch_products_batch(redis, wb_ids, session=session)
            except Exception:
                logger.exception("BATCH_FETCH failed, falling back to individual fetches")

        # ── Обработка треков ─────────────────────────────────────────────────
        for t in tracks:
            user_tg_id = t.user.tg_user_id
            track_id = t.id
            track_title = t.title
            base_min = _base_interval_for_track(t, cfg_free, cfg_pro)
            try:
                # Пробуем результат из батча, иначе индивидуальный запрос
                snap = batch_results.get(t.wb_item_id)
                if snap is None:
                    snap = await fetch_product(redis, t.wb_item_id, session=session)
                if not snap:
                    continue

                logger.info(
                    "TRACK_CHECK: track_id=%s wb_item_id=%s last_in_stock=%s snap.in_stock=%s",
                    t.id, t.wb_item_id, t.last_in_stock, snap.in_stock,
                )

                db_session.add(
                    SnapshotModel(
                        track_id=t.id,
                        price_current=snap.price,
                        rating_current=snap.rating,
                        reviews_current=snap.reviews,
                        in_stock=snap.in_stock,
                        qty_current=snap.total_qty,
                        sizes=snap.sizes,
                    )
                )

                prev_in_stock = t.last_in_stock
                prev_price = t.last_price
                prev_qty = t.last_qty
                prev_sizes = t.last_sizes

                t.last_price = snap.price
                t.last_rating = snap.rating
                t.last_reviews = snap.reviews
                t.last_in_stock = snap.in_stock
                t.last_qty = snap.total_qty
                t.last_sizes = snap.sizes
                t.last_checked_at = now_naive
                t.error_count = 0

                # Обновляем счётчик изменений цены
                price_changed = (
                    prev_price is not None
                    and snap.price is not None
                    and snap.price != prev_price
                )
                if price_changed:
                    t.price_change_count = (t.price_change_count or 0) + 1
                    t.last_price_changed_at = now_naive

                # Обновляем адаптивный интервал
                t.check_interval_min = _adaptive_interval(t, base_min)

                logger.info(
                    "TRACK_UPDATED: track_id=%s last_in_stock=%s interval=%dmin (base=%dmin, changes=%d)",
                    t.id, t.last_in_stock, t.check_interval_min, base_min, t.price_change_count or 0,
                )

                # События и уведомления
                async with db_session.begin_nested():
                    events: list[str] = []

                    if (
                        t.watch_price_fluctuation
                        and price_changed
                    ):
                        events.append(_msg("price_changed", old=str(prev_price), new=str(snap.price)))

                    if t.watch_stock and prev_in_stock is False and snap.in_stock:
                        logger.info(
                            "IN_STOCK_EVENT: track_id=%s prev=%s curr=%s",
                            t.id, prev_in_stock, snap.in_stock,
                        )
                        events.append(_msg("in_stock", track_id=t.id))

                    if (
                        t.user.plan in {"pro", "pro_plus"}
                        and t.watch_qty
                        and prev_qty is not None
                        and snap.total_qty is not None
                        and prev_qty != snap.total_qty
                    ):
                        direction = "⬆️" if snap.total_qty > prev_qty else "⬇️"
                        events.append(_msg("stock_changed", direction=direction,
                                           old=str(prev_qty), new=str(snap.total_qty)))

                    if t.watch_sizes:
                        watched, prev, curr = (
                            set(t.watch_sizes),
                            set(prev_sizes or []),
                            set(snap.sizes),
                        )
                        appeared = sorted(watched & curr - prev)
                        gone = sorted(watched & prev - curr)
                        if appeared:
                            events.append(_msg("sizes_appeared", sizes=", ".join(appeared)))
                        if gone:
                            events.append(_msg("sizes_gone", sizes=", ".join(gone)))

                    for ev in events:
                        h = _hash_event(t.id, "event", ev)
                        inserted = await log_event(db_session, t.id, "event", h)
                        if not inserted:
                            continue
                        await bot.send_message(
                            user_tg_id,
                            tx.WORKER_NOTIFY_TEMPLATE.format(
                                title=track_title, event=ev, url=t.url,
                            ),
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                        )
                        t.last_notified_at = now_naive

            except Exception:
                logger.exception("WB monitor track failed (track_id=%s)", track_id)
                try:
                    result = await db_session.execute(
                        update(TrackModel)
                        .where(TrackModel.id == track_id)
                        .values(error_count=TrackModel.error_count + 1)
                        .returning(TrackModel.error_count, TrackModel.is_active)
                    )
                    row = result.first()
                    if row and row.error_count >= ERROR_LIMIT and row.is_active:
                        await db_session.execute(
                            update(TrackModel)
                            .where(TrackModel.id == track_id)
                            .values(is_active=False)
                        )
                        try:
                            await bot.send_message(
                                user_tg_id,
                                _msg("paused_error", id=str(track_id), title=track_title),
                                link_preview_options=LinkPreviewOptions(is_disabled=True),
                            )
                        except Exception:
                            logger.exception("WB monitor pause notify failed (track_id=%s)", track_id)
                except Exception:
                    logger.exception("WB monitor error handler failed (track_id=%s)", track_id)

        await db_session.commit()
        await WorkerStateRD.set_heartbeat(redis, now_naive.isoformat())


async def start_worker(
    db_pool: async_sessionmaker[AsyncSession],
    redis: "Redis",
    bot: "Bot",
) -> asyncio.Task:
    async def _loop() -> None:
        logger.info("WB monitor worker started (adaptive mode)")
        last_expiry_check: date | None = None
        async with ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as http:
            while True:
                started = datetime.now(UTC)
                try:
                    await run_cycle(db_pool=db_pool, redis=redis, bot=bot, session=http)
                    await WorkerStateRD.set_cycle_duration(
                        redis, (datetime.now(UTC) - started).total_seconds()
                    )
                except Exception:
                    logger.exception("WB monitor cycle failed")

                try:
                    now_naive = datetime.now(UTC).replace(tzinfo=None)
                    if last_expiry_check != now_naive.date():
                        async with db_pool() as db_session, db_session.begin():
                            cfg = await get_runtime_config(db_session)
                            expired = await expire_pro_users(
                                db_session, now_naive,
                                redis=redis,
                                free_interval_min=cfg.free_interval_min,
                            )
                        if expired:
                            logger.info("Expired %s pro users", expired)
                        last_expiry_check = now_naive.date()
                except Exception:
                    logger.exception("WB monitor pro expiry check failed")

                await asyncio.sleep(60)

    return asyncio.create_task(_loop(), name="wb-monitor-worker")
