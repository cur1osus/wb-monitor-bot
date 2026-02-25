from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from aiogram.types import LinkPreviewOptions
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import SnapshotModel
from bot.db.redis import WorkerStateRD
from bot.services.repository import (
    due_tracks_python_safe,
    expire_pro_users,
    get_runtime_config,
    is_duplicate_event,
    log_event,
)
from bot.services.wb_client import fetch_product

if TYPE_CHECKING:
    from aiogram import Bot
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

ERROR_LIMIT = 5

_MSG: dict[str, str] = {
    "price_target": "💸 Цена достигла цели: {price} ₽ (цель: {target} ₽)",
    "price_drop": "📉 Падение цены на {percent}%: {old} ₽ → {new} ₽",
    "in_stock": "✅ Товар снова в наличии",
    "stock_changed": "📦 Остаток изменился {direction}: {old} → {new}",
    "sizes_appeared": "📏 Появились размеры: {sizes}",
    "sizes_gone": "📏 Исчезли размеры: {sizes}",
    "paused_error": "⚠️ Трек #{id} поставлен на паузу из-за ошибок.\n{title}",
}


def _msg(key: str, **kw: str | int) -> str:
    return _MSG[key].format(**kw)


def _price_drop_percent(old: Decimal | None, new: Decimal | None) -> int:
    if old is None or new is None or old <= 0 or new >= old:
        return 0
    return int(((old - new) / old) * 100)


def _hash_event(track_id: int, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{track_id}:{kind}:{payload}".encode()).hexdigest()[:48]


async def run_cycle(
    db_pool: async_sessionmaker[AsyncSession],
    redis: "Redis",
    bot: "Bot",
    session: ClientSession,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_pool() as db_session:
        tracks = await due_tracks_python_safe(db_session, now)

        for t in tracks:
            user_tg_id = t.user.tg_user_id
            track_id = t.id
            track_title = t.title
            try:
                snap = await fetch_product(redis, t.wb_item_id, session=session)
                if not snap:
                    continue

                async with db_session.begin_nested():
                    events: list[str] = []

                    if (
                        t.target_price is not None
                        and snap.price is not None
                        and snap.price <= t.target_price
                    ):
                        events.append(
                            _msg(
                                "price_target",
                                price=str(snap.price),
                                target=str(t.target_price),
                            )
                        )

                    drop = _price_drop_percent(t.last_price, snap.price)
                    if t.target_drop_percent and drop >= t.target_drop_percent:
                        events.append(
                            _msg(
                                "price_drop",
                                percent=str(drop),
                                old=str(t.last_price),
                                new=str(snap.price),
                            )
                        )

                    if t.watch_stock and t.last_in_stock is False and snap.in_stock:
                        events.append(_msg("in_stock"))

                    if (
                        t.user.plan == "pro"
                        and t.watch_qty
                        and t.last_qty is not None
                        and snap.total_qty is not None
                        and t.last_qty != snap.total_qty
                    ):
                        direction = "⬆️" if snap.total_qty > t.last_qty else "⬇️"
                        events.append(
                            _msg(
                                "stock_changed",
                                direction=direction,
                                old=str(t.last_qty),
                                new=str(snap.total_qty),
                            )
                        )

                    if t.watch_sizes:
                        watched, prev, curr = (
                            set(t.watch_sizes),
                            set(t.last_sizes or []),
                            set(snap.sizes),
                        )
                        appeared = sorted(watched & curr - prev)
                        gone = sorted(watched & prev - curr)
                        if appeared:
                            events.append(
                                _msg("sizes_appeared", sizes=", ".join(appeared))
                            )
                        if gone:
                            events.append(_msg("sizes_gone", sizes=", ".join(gone)))

                    for ev in events:
                        h = _hash_event(t.id, "event", ev)
                        if await is_duplicate_event(db_session, t.id, h):
                            continue
                        await log_event(db_session, t.id, "event", h)
                        await bot.send_message(
                            user_tg_id,
                            f"🔔 <b>{track_title}</b>\n{ev}\n{t.url}",
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                        )
                        t.last_notified_at = now

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
                    t.last_price = snap.price
                    t.last_rating = snap.rating
                    t.last_reviews = snap.reviews
                    t.last_in_stock = snap.in_stock
                    t.last_qty = snap.total_qty
                    t.last_sizes = snap.sizes
                    t.last_checked_at = now
                    t.error_count = 0

            except Exception:
                logger.exception("WB monitor track failed (track_id=%s)", track_id)
                async with db_session.begin_nested():
                    t.error_count = (t.error_count or 0) + 1
                    if t.error_count >= ERROR_LIMIT and t.is_active:
                        t.is_active = False
                        try:
                            await bot.send_message(
                                user_tg_id,
                                _msg(
                                    "paused_error", id=str(track_id), title=track_title
                                ),
                                link_preview_options=LinkPreviewOptions(
                                    is_disabled=True
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "WB monitor pause notify failed (track_id=%s)", track_id
                            )

        await db_session.commit()

        # Heartbeat в Redis
        await WorkerStateRD.set_heartbeat(redis, now.isoformat())


async def start_worker(
    db_pool: async_sessionmaker[AsyncSession],
    redis: "Redis",
    bot: "Bot",
) -> asyncio.Task:
    async def _loop() -> None:
        logger.info("WB monitor worker started")
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

                await asyncio.sleep(60)

    return asyncio.create_task(_loop(), name="wb-monitor-worker")
