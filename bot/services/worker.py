"""worker.py — adaptive WB price monitor worker."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from aiogram.types import LinkPreviewOptions
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import text as tx
from bot.db.models import SnapshotModel, TrackModel
from bot.db.redis import WorkerStateRD
from bot.enums import UserPlan
from bot.services.repository import (
    calc_next_check_at,
    delete_alert_events_by_hashes,
    expire_pro_users,
    get_due_tracks_batch,
    get_next_due_at,
    get_runtime_config,
    log_event,
    mark_tracks_last_notified,
)
from bot.services.wb_client import fetch_product, fetch_products_batch

if TYPE_CHECKING:
    from aiogram import Bot
    from redis.asyncio import Redis
    from bot.services.wb_client import WbProductSnapshot

logger = logging.getLogger(__name__)

ERROR_LIMIT = 5
WORKER_BATCH_SIZE = 200
WORKER_CANDIDATE_MULTIPLIER = 4
WORKER_NOTIFY_CONCURRENCY = 8
WORKER_FETCH_MISS_CONCURRENCY = 8
WORKER_IDLE_SLEEP_SEC = 300
WORKER_BUSY_SLEEP_SEC = 2

# ─── Ночная пауза (МСК = UTC+3) ───────────────────────────────────────────────
_NIGHT_START_UTC_HOUR = 22  # 01:00 МСК
_NIGHT_END_UTC_HOUR = 4  # 07:00 МСК


@dataclass(slots=True)
class PendingWorkerNotification:
    tg_user_id: int
    text: str
    track_id: int | None = None
    event_hash: str | None = None


@dataclass(slots=True)
class WorkerCycleResult:
    processed: int
    has_more_due: bool
    next_due_at: datetime | None
    night_mode: bool


def _is_night(now_utc: datetime) -> bool:
    """True if current UTC time falls in WB's low-activity night window (01-07 МСК)."""
    hour = now_utc.hour
    return hour >= _NIGHT_START_UTC_HOUR or hour < _NIGHT_END_UTC_HOUR


def _adaptive_interval(track: TrackModel, base_min: int) -> int:
    """Calculate adaptive check_interval_min based on price change history."""
    count = track.price_change_count or 0

    if count >= 7:
        coeff = 0.5
    elif count >= 3:
        coeff = 0.75
    else:
        coeff = 1.5

    if track.last_price_changed_at is not None:
        days_since_change = (
            datetime.now(UTC).replace(tzinfo=None) - track.last_price_changed_at
        ).days
        if days_since_change >= 14:
            coeff = min(coeff * 1.5, 2.0)
    elif track.price_change_count == 0 and track.created_at is not None:
        days_since_created = (
            datetime.now(UTC).replace(tzinfo=None) - track.created_at
        ).days
        if days_since_created >= 14:
            coeff = min(coeff * 1.5, 2.0)

    result = int(base_min * coeff)
    return max(1, min(result, base_min * 3))


def _track_priority(track: TrackModel) -> tuple[int, int]:
    """Lower number = higher priority."""
    plan = track.user.plan if track.user else UserPlan.FREE.value
    is_paid = plan in {UserPlan.PRO.value, UserPlan.PRO_PLUS.value}
    out_of_stock_watch = track.watch_stock and track.last_in_stock is False

    if out_of_stock_watch:
        return (0, 0)
    if is_paid:
        return (1, -(track.price_change_count or 0))
    if (track.price_change_count or 0) >= 3:
        return (2, -(track.price_change_count or 0))
    return (3, 0)


def _priority_bucket(track: TrackModel) -> int:
    return _track_priority(track)[0]


def _msg(key: str, **kw: str | int) -> str:
    return tx.WORKER_EVENTS[key].format(**kw)


def _hash_event(track_id: int, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{track_id}:{kind}:{payload}".encode()).hexdigest()[:48]


def _base_interval_for_track(track: TrackModel, cfg_free: int, cfg_pro: int) -> int:
    plan = track.user.plan if track.user else UserPlan.FREE.value
    return (
        cfg_pro if plan in {UserPlan.PRO.value, UserPlan.PRO_PLUS.value} else cfg_free
    )


def _pick_next_track(
    bucket: deque[TrackModel],
    *,
    last_user_id: int | None,
) -> TrackModel | None:
    if not bucket:
        return None
    if last_user_id is None or len(bucket) == 1:
        return bucket.popleft()
    for idx, track in enumerate(bucket):
        if track.user_id != last_user_id:
            del bucket[idx]
            return track
    return bucket.popleft()


def _fair_order_tracks(tracks: list[TrackModel], *, limit: int) -> list[TrackModel]:
    buckets: dict[int, deque[TrackModel]] = {idx: deque() for idx in range(4)}
    ordered_candidates = sorted(
        tracks,
        key=lambda item: (
            _priority_bucket(item),
            item.next_check_at or datetime.min,
            -int(item.price_change_count or 0),
            item.id,
        ),
    )
    for track in ordered_candidates:
        buckets[_priority_bucket(track)].append(track)

    schedule = [0, 1, 0, 2, 1, 0, 3, 1, 2]
    ordered: list[TrackModel] = []
    last_user_id: int | None = None

    while len(ordered) < limit and any(buckets.values()):
        progressed = False
        for bucket_id in schedule:
            track = _pick_next_track(buckets[bucket_id], last_user_id=last_user_id)
            if track is None:
                continue
            ordered.append(track)
            last_user_id = track.user_id
            progressed = True
            if len(ordered) >= limit:
                break
        if not progressed:
            break

    return ordered


async def _fetch_missing_products(
    *,
    redis: "Redis",
    session: ClientSession,
    tracks: list[TrackModel],
    batch_results: dict[int, "WbProductSnapshot"],
) -> None:
    missing_tracks = [
        track for track in tracks if track.wb_item_id not in batch_results
    ]
    if not missing_tracks:
        return

    semaphore = asyncio.Semaphore(WORKER_FETCH_MISS_CONCURRENCY)

    async def _fetch_one(track: TrackModel) -> tuple[int, "WbProductSnapshot | None"]:
        async with semaphore:
            snap = await fetch_product(redis, track.wb_item_id, session=session)
            return track.wb_item_id, snap

    results = await asyncio.gather(*(_fetch_one(track) for track in missing_tracks))
    for wb_item_id, snap in results:
        if snap is not None:
            batch_results[wb_item_id] = snap


async def _dispatch_notifications(
    *,
    bot: "Bot",
    notifications: list[PendingWorkerNotification],
) -> tuple[list[int], list[str]]:
    if not notifications:
        return [], []

    semaphore = asyncio.Semaphore(WORKER_NOTIFY_CONCURRENCY)

    async def _send(
        notification: PendingWorkerNotification,
    ) -> tuple[PendingWorkerNotification, bool]:
        async with semaphore:
            try:
                await bot.send_message(
                    notification.tg_user_id,
                    notification.text,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return notification, True
            except Exception:
                logger.exception(
                    "Worker notification send failed (track_id=%s tg_user_id=%s)",
                    notification.track_id,
                    notification.tg_user_id,
                )
                return notification, False

    results = await asyncio.gather(*(_send(item) for item in notifications))
    success_track_ids = [
        item.track_id
        for item, ok in results
        if ok and item.track_id is not None and item.event_hash is not None
    ]
    failed_event_hashes = [
        item.event_hash
        for item, ok in results
        if not ok and item.event_hash is not None
    ]
    return success_track_ids, failed_event_hashes


def _seconds_until_night_end(now_utc: datetime) -> int:
    if now_utc.hour >= _NIGHT_START_UTC_HOUR:
        night_end = (now_utc + timedelta(days=1)).replace(
            hour=_NIGHT_END_UTC_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        night_end = now_utc.replace(
            hour=_NIGHT_END_UTC_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    return max(5, int((night_end - now_utc).total_seconds()))


def _compute_sleep_seconds(
    *,
    now_utc: datetime,
    next_due_at: datetime | None,
    has_more_due: bool,
    night_mode: bool,
) -> int:
    if has_more_due:
        return WORKER_BUSY_SLEEP_SEC + (now_utc.second % 2)
    if next_due_at is None:
        if night_mode:
            return min(WORKER_IDLE_SLEEP_SEC, _seconds_until_night_end(now_utc))
        return 60

    delta_sec = int((next_due_at - now_utc.replace(tzinfo=None)).total_seconds())
    if delta_sec <= 0:
        return WORKER_BUSY_SLEEP_SEC

    jitter_sec = now_utc.second % 3
    return max(5, min(WORKER_IDLE_SLEEP_SEC, delta_sec + jitter_sec))


async def run_cycle(
    db_pool: async_sessionmaker[AsyncSession],
    redis: "Redis",
    bot: "Bot",
    session: ClientSession,
) -> WorkerCycleResult:
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    night_mode = _is_night(now)
    stock_only = night_mode

    notifications: list[PendingWorkerNotification] = []
    processed = 0
    has_more_due = False
    next_due_at: datetime | None = None

    async with db_pool() as db_session:
        cfg = await get_runtime_config(db_session)
        cfg_free = cfg.free_interval_min
        cfg_pro = cfg.pro_interval_min

        candidate_limit = WORKER_BATCH_SIZE * WORKER_CANDIDATE_MULTIPLIER + 1
        candidates = await get_due_tracks_batch(
            db_session,
            now_naive,
            limit=candidate_limit,
            stock_only=stock_only,
        )
        if night_mode and candidates:
            logger.debug(
                "NIGHT_MODE: %d tracks remain (out-of-stock watchers)", len(candidates)
            )

        has_more_due = len(candidates) > WORKER_BATCH_SIZE
        tracks = _fair_order_tracks(
            candidates[: candidate_limit - 1], limit=WORKER_BATCH_SIZE
        )

        wb_ids = [track.wb_item_id for track in tracks]
        batch_results: dict[int, WbProductSnapshot] = {}
        if wb_ids:
            try:
                batch_results = await fetch_products_batch(
                    redis, wb_ids, session=session
                )
            except Exception:
                logger.exception(
                    "BATCH_FETCH failed, falling back to individual fetches"
                )
            await _fetch_missing_products(
                redis=redis,
                session=session,
                tracks=tracks,
                batch_results=batch_results,
            )

        for track in tracks:
            user_tg_id = track.user.tg_user_id
            base_min = _base_interval_for_track(track, cfg_free, cfg_pro)
            try:
                snap = batch_results.get(track.wb_item_id)
                if snap is None:
                    continue

                logger.info(
                    "TRACK_CHECK: track_id=%s wb_item_id=%s last_in_stock=%s snap.in_stock=%s",
                    track.id,
                    track.wb_item_id,
                    track.last_in_stock,
                    snap.in_stock,
                )

                async with db_session.begin_nested():
                    db_session.add(
                        SnapshotModel(
                            track_id=track.id,
                            price_current=snap.price,
                            rating_current=snap.rating,
                            reviews_current=snap.reviews,
                            in_stock=snap.in_stock,
                            qty_current=snap.total_qty,
                            sizes=snap.sizes,
                        )
                    )

                    prev_in_stock = track.last_in_stock
                    prev_price = track.last_price
                    prev_qty = track.last_qty
                    prev_sizes = track.last_sizes

                    track.last_price = snap.price
                    track.last_rating = snap.rating
                    track.last_reviews = snap.reviews
                    track.last_in_stock = snap.in_stock
                    track.last_qty = snap.total_qty
                    track.last_sizes = snap.sizes
                    track.last_checked_at = now_naive
                    track.error_count = 0

                    price_changed = (
                        prev_price is not None
                        and snap.price is not None
                        and snap.price != prev_price
                    )
                    if price_changed:
                        track.price_change_count = (track.price_change_count or 0) + 1
                        track.last_price_changed_at = now_naive

                    track.check_interval_min = _adaptive_interval(track, base_min)
                    track.next_check_at = calc_next_check_at(
                        track_id=track.id,
                        base_time=now_naive,
                        interval_min=track.check_interval_min,
                    )

                    logger.info(
                        "TRACK_UPDATED: track_id=%s last_in_stock=%s interval=%dmin (base=%dmin, changes=%d)",
                        track.id,
                        track.last_in_stock,
                        track.check_interval_min,
                        base_min,
                        track.price_change_count or 0,
                    )

                    events: list[str] = []
                    if track.watch_price_fluctuation and price_changed:
                        events.append(
                            _msg(
                                "price_changed",
                                old=str(prev_price),
                                new=str(snap.price),
                            )
                        )

                    if track.watch_stock and prev_in_stock is False and snap.in_stock:
                        logger.info(
                            "IN_STOCK_EVENT: track_id=%s prev=%s curr=%s",
                            track.id,
                            prev_in_stock,
                            snap.in_stock,
                        )
                        events.append(_msg("in_stock", track_id=track.id))

                    if (
                        track.user.plan in {UserPlan.PRO.value, UserPlan.PRO_PLUS.value}
                        and track.watch_qty
                        and prev_qty is not None
                        and snap.total_qty is not None
                        and prev_qty != snap.total_qty
                    ):
                        direction = "⬆️" if snap.total_qty > prev_qty else "⬇️"
                        events.append(
                            _msg(
                                "stock_changed",
                                direction=direction,
                                old=str(prev_qty),
                                new=str(snap.total_qty),
                            )
                        )

                    if track.watch_sizes:
                        watched = set(track.watch_sizes)
                        prev = set(prev_sizes or [])
                        curr = set(snap.sizes)
                        appeared = sorted(watched & curr - prev)
                        gone = sorted(watched & prev - curr)
                        if appeared:
                            events.append(
                                _msg("sizes_appeared", sizes=", ".join(appeared))
                            )
                        if gone:
                            events.append(_msg("sizes_gone", sizes=", ".join(gone)))

                    for event_text in events:
                        event_hash = _hash_event(track.id, "event", event_text)
                        inserted = await log_event(
                            db_session,
                            track.id,
                            "event",
                            event_hash,
                        )
                        if not inserted:
                            continue
                        notifications.append(
                            PendingWorkerNotification(
                                tg_user_id=user_tg_id,
                                track_id=track.id,
                                event_hash=event_hash,
                                text=tx.WORKER_NOTIFY_TEMPLATE.format(
                                    title=track.title,
                                    event=event_text,
                                    url=track.url,
                                ),
                            )
                        )

                processed += 1

            except Exception:
                logger.exception("WB monitor track failed (track_id=%s)", track.id)
                try:
                    result = await db_session.execute(
                        update(TrackModel)
                        .where(TrackModel.id == track.id)
                        .values(error_count=TrackModel.error_count + 1)
                        .returning(TrackModel.error_count, TrackModel.is_active)
                    )
                    row = result.first()
                    if row and row.error_count >= ERROR_LIMIT and row.is_active:
                        await db_session.execute(
                            update(TrackModel)
                            .where(TrackModel.id == track.id)
                            .values(is_active=False, next_check_at=None)
                        )
                        notifications.append(
                            PendingWorkerNotification(
                                tg_user_id=user_tg_id,
                                text=_msg(
                                    "paused_error",
                                    id=str(track.id),
                                    title=track.title,
                                ),
                            )
                        )
                except Exception:
                    logger.exception(
                        "WB monitor error handler failed (track_id=%s)",
                        track.id,
                    )

        await db_session.commit()
        await WorkerStateRD.set_heartbeat(redis, now_naive.isoformat())
        next_due_at = await get_next_due_at(db_session, stock_only=stock_only)

    success_track_ids, failed_event_hashes = await _dispatch_notifications(
        bot=bot,
        notifications=notifications,
    )

    if success_track_ids or failed_event_hashes:
        async with db_pool() as notify_session:
            if success_track_ids:
                await mark_tracks_last_notified(
                    notify_session,
                    track_ids=list(dict.fromkeys(success_track_ids)),
                    notified_at=now_naive,
                )
            if failed_event_hashes:
                await delete_alert_events_by_hashes(
                    notify_session,
                    event_hashes=list(dict.fromkeys(failed_event_hashes)),
                )
            await notify_session.commit()

    return WorkerCycleResult(
        processed=processed,
        has_more_due=has_more_due,
        next_due_at=next_due_at,
        night_mode=night_mode,
    )


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
                    cycle_result = await run_cycle(
                        db_pool=db_pool,
                        redis=redis,
                        bot=bot,
                        session=http,
                    )
                    await WorkerStateRD.set_cycle_duration(
                        redis, (datetime.now(UTC) - started).total_seconds()
                    )
                except Exception:
                    logger.exception("WB monitor cycle failed")
                    cycle_result = WorkerCycleResult(
                        processed=0,
                        has_more_due=False,
                        next_due_at=None,
                        night_mode=_is_night(datetime.now(UTC)),
                    )

                try:
                    now_naive = datetime.now(UTC).replace(tzinfo=None)
                    if last_expiry_check != now_naive.date():
                        async with db_pool() as db_session, db_session.begin():
                            cfg = await get_runtime_config(db_session)
                            expired = await expire_pro_users(
                                db_session,
                                now_naive,
                                redis=redis,
                                free_interval_min=cfg.free_interval_min,
                            )
                        if expired:
                            logger.info("Expired %s pro users", expired)
                        last_expiry_check = now_naive.date()
                except Exception:
                    logger.exception("WB monitor pro expiry check failed")

                sleep_for = _compute_sleep_seconds(
                    now_utc=datetime.now(UTC),
                    next_due_at=cycle_result.next_due_at,
                    has_more_due=cycle_result.has_more_due,
                    night_mode=cycle_result.night_mode,
                )
                await asyncio.sleep(sleep_for)

    return asyncio.create_task(_loop(), name="wb-monitor-worker")
