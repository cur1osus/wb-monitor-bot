from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from secrets import token_urlsafe
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import (
    AlertLogModel,
    MonitorUserModel,
    ReferralRewardModel,
    RuntimeConfigModel,
    SnapshotModel,
    TrackModel,
)
from bot.db.redis import MonitorUserRD
from bot.services.config import (
    CHEAP_MATCH_PERCENT_DEFAULT,
    FREE_INTERVAL,
    PRO_INTERVAL,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(slots=True)
class AdminStats:
    days: int
    total_users: int
    new_users: int
    pro_users: int
    total_tracks: int
    active_tracks: int
    new_tracks: int
    checks_count: int
    alerts_count: int
    cheap_scans_count: int
    reviews_scans_count: int


@dataclass(slots=True)
class RuntimeConfigView:
    free_interval_min: int
    pro_interval_min: int
    cheap_match_percent: int
    free_daily_ai_limit: int
    pro_daily_ai_limit: int
    review_sample_limit_per_side: int
    analysis_model: str


def _new_ref_code() -> str:
    return token_urlsafe(6).replace("-", "").replace("_", "").upper()[:10]


async def _ensure_referral_code(session: AsyncSession, user: MonitorUserModel) -> None:
    if user.referral_code:
        return
    while True:
        code = _new_ref_code()
        occupied = await session.scalar(
            select(exists().where(MonitorUserModel.referral_code == code))
        )
        if not occupied:
            user.referral_code = code
            return


async def get_or_create_monitor_user(
    session: AsyncSession,
    tg_user_id: int,
    username: str | None,
    redis: "Redis | None" = None,
) -> MonitorUserModel:
    """Получить или создать пользователя. При изменении — инвалидирует Redis-кэш."""
    user = await session.scalar(
        select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
    )
    if user:
        user.username = username
        await _ensure_referral_code(session, user)
        return user

    user = MonitorUserModel(tg_user_id=tg_user_id, username=username)
    session.add(user)
    await session.flush()
    await _ensure_referral_code(session, user)

    # Инвалидируем кэш при создании (на случай если был промах)
    if redis:
        await MonitorUserRD.invalidate(redis, tg_user_id)

    return user


async def bind_user_referrer_by_code(
    session: AsyncSession,
    user: MonitorUserModel,
    referral_code: str,
    redis: "Redis | None" = None,
) -> bool:
    if user.referred_by_tg_user_id:
        return False

    code = referral_code.strip().upper()
    if not code:
        return False

    referrer = await session.scalar(
        select(MonitorUserModel).where(MonitorUserModel.referral_code == code)
    )
    if not referrer or referrer.tg_user_id == user.tg_user_id:
        return False

    user.referred_by_tg_user_id = referrer.tg_user_id

    if redis:
        await MonitorUserRD.invalidate(redis, user.tg_user_id)

    return True


async def get_monitor_user_by_tg_id(
    session: AsyncSession, tg_user_id: int
) -> MonitorUserModel | None:
    return await session.scalar(
        select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
    )


async def add_referral_reward_once(
    session: AsyncSession,
    *,
    referrer_user_id: int,
    invited_user_id: int,
    invited_tg_user_id: int,
    payment_charge_id: str,
    rewarded_days: int = 7,
) -> bool:
    exists_row = await session.scalar(
        select(ReferralRewardModel.id).where(
            ReferralRewardModel.payment_charge_id == payment_charge_id
        )
    )
    if exists_row:
        return False

    session.add(
        ReferralRewardModel(
            referrer_user_id=referrer_user_id,
            invited_user_id=invited_user_id,
            invited_tg_user_id=invited_tg_user_id,
            payment_charge_id=payment_charge_id,
            rewarded_days=rewarded_days,
        )
    )
    return True


async def count_user_tracks(
    session: AsyncSession, user_id: int, active_only: bool = True
) -> int:
    query = select(func.count(TrackModel.id)).where(
        TrackModel.user_id == user_id,
        TrackModel.is_deleted.is_(False),
    )
    if active_only:
        query = query.where(TrackModel.is_active.is_(True))
    count = await session.scalar(query)
    return int(count or 0)


async def get_runtime_config(session: AsyncSession) -> RuntimeConfigModel:
    cfg = await session.get(RuntimeConfigModel, 1)
    if cfg is not None:
        return cfg

    cfg = RuntimeConfigModel(
        id=1,
        free_interval_min=FREE_INTERVAL,
        pro_interval_min=PRO_INTERVAL,
        cheap_match_percent=CHEAP_MATCH_PERCENT_DEFAULT,
        free_daily_ai_limit=3,
        pro_daily_ai_limit=10,
        review_sample_limit_per_side=50,
        analysis_model="qwen/qwen3-32b",
    )
    session.add(cfg)
    await session.flush()
    return cfg


def runtime_config_view(cfg: RuntimeConfigModel) -> RuntimeConfigView:
    return RuntimeConfigView(
        free_interval_min=int(cfg.free_interval_min),
        pro_interval_min=int(cfg.pro_interval_min),
        cheap_match_percent=int(cfg.cheap_match_percent),
        free_daily_ai_limit=int(cfg.free_daily_ai_limit),
        pro_daily_ai_limit=int(cfg.pro_daily_ai_limit),
        review_sample_limit_per_side=int(cfg.review_sample_limit_per_side),
        analysis_model=str(cfg.analysis_model),
    )


async def apply_runtime_intervals(
    session: AsyncSession,
    *,
    free_interval_min: int,
    pro_interval_min: int,
) -> None:
    pro_user_ids = select(MonitorUserModel.id).where(MonitorUserModel.plan == "pro")
    free_user_ids = select(MonitorUserModel.id).where(MonitorUserModel.plan != "pro")

    await session.execute(
        update(TrackModel)
        .where(TrackModel.user_id.in_(pro_user_ids), TrackModel.is_deleted.is_(False))
        .values(check_interval_min=pro_interval_min)
    )
    await session.execute(
        update(TrackModel)
        .where(TrackModel.user_id.in_(free_user_ids), TrackModel.is_deleted.is_(False))
        .values(check_interval_min=free_interval_min)
    )


async def expire_pro_users(
    session: AsyncSession,
    now: datetime,
    redis: "Redis | None" = None,
    free_interval_min: int = FREE_INTERVAL,
) -> int:
    stmt_ids = select(MonitorUserModel.id, MonitorUserModel.tg_user_id).where(
        MonitorUserModel.plan == "pro",
        MonitorUserModel.pro_expires_at.is_not(None),
        MonitorUserModel.pro_expires_at < now,
    )
    rows = (await session.execute(stmt_ids)).all()

    if not rows:
        return 0

    user_ids = [r[0] for r in rows]
    tg_ids = [r[1] for r in rows]

    await session.execute(
        update(MonitorUserModel)
        .where(MonitorUserModel.id.in_(user_ids))
        .values(plan="free", pro_expires_at=None),
    )

    await session.execute(
        update(TrackModel)
        .where(TrackModel.user_id.in_(user_ids))
        .values(check_interval_min=free_interval_min),
    )

    # Инвалидация Redis-кэша для всех сменивших план
    if redis:
        for tg_id in tg_ids:
            await MonitorUserRD.invalidate(redis, tg_id)

    return len(user_ids)


async def set_user_tracks_interval(
    session: AsyncSession, user_id: int, interval_min: int
) -> int:
    result = await session.execute(
        update(TrackModel)
        .where(TrackModel.user_id == user_id)
        .values(check_interval_min=interval_min)
    )
    return int(result.rowcount or 0)


async def create_track(
    session: AsyncSession,
    user_id: int,
    wb_item_id: int,
    url: str,
    title: str,
    price: Decimal | None,
    in_stock: bool,
    qty: int | None,
    sizes: list[str],
    rating: Decimal | None,
    reviews: int | None,
    check_interval_min: int,
) -> TrackModel:
    track = TrackModel(
        user_id=user_id,
        wb_item_id=wb_item_id,
        url=url,
        title=title,
        check_interval_min=check_interval_min,
        watch_qty=False,
        last_price=price,
        last_rating=rating,
        last_reviews=reviews,
        last_in_stock=in_stock,
        last_qty=qty,
        last_sizes=sizes,
        last_checked_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(track)
    await session.flush()

    session.add(
        SnapshotModel(
            track_id=track.id,
            price_current=price,
            rating_current=rating,
            reviews_current=reviews,
            in_stock=in_stock,
            qty_current=qty,
            sizes=sizes,
        )
    )
    return track


async def get_user_tracks(session: AsyncSession, user_id: int) -> list[TrackModel]:
    rows = await session.scalars(
        select(TrackModel)
        .where(TrackModel.user_id == user_id, TrackModel.is_deleted.is_(False))
        .order_by(TrackModel.created_at.desc())
    )
    return list(rows)


async def toggle_track_active(
    session: AsyncSession, track_id: int, is_active: bool
) -> None:
    await session.execute(
        update(TrackModel).where(TrackModel.id == track_id).values(is_active=is_active)
    )


async def delete_track(session: AsyncSession, track_id: int) -> None:
    await session.execute(
        update(TrackModel)
        .where(TrackModel.id == track_id)
        .values(is_deleted=True, is_active=False)
    )


async def get_user_track_by_id(
    session: AsyncSession, track_id: int
) -> TrackModel | None:
    return await session.scalar(
        select(TrackModel).where(
            TrackModel.id == track_id, TrackModel.is_deleted.is_(False)
        )
    )


async def due_tracks_python_safe(
    session: AsyncSession, now: datetime
) -> list[TrackModel]:
    rows = await session.scalars(
        select(TrackModel)
        .options(selectinload(TrackModel.user))
        .where(TrackModel.is_active.is_(True), TrackModel.is_deleted.is_(False))
    )
    out: list[TrackModel] = []
    for t in list(rows):
        if t.last_checked_at is None or t.last_checked_at <= now - timedelta(
            minutes=t.check_interval_min
        ):
            out.append(t)
    return out


async def is_duplicate_event(
    session: AsyncSession, track_id: int, event_hash: str, within_hours: int = 24
) -> bool:
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=within_hours)
    existing = await session.scalar(
        select(AlertLogModel.id).where(
            AlertLogModel.track_id == track_id,
            AlertLogModel.event_hash == event_hash,
            AlertLogModel.sent_at >= since,
        )
    )
    return existing is not None


async def log_event(
    session: AsyncSession, track_id: int, event_type: str, event_hash: str
) -> None:
    session.add(
        AlertLogModel(track_id=track_id, event_type=event_type, event_hash=event_hash)
    )


async def get_admin_stats(session: AsyncSession, *, days: int) -> AdminStats:
    # Все таймстемпы в БД храним как naive UTC. Для «дня» считаем границу по Москве.
    now_utc = datetime.now(UTC)
    now = now_utc.replace(tzinfo=None)

    msk = ZoneInfo("Europe/Moscow")
    now_msk = now_utc.astimezone(msk)
    today_start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1 день: только с 00:00 сегодняшнего дня (MSK).
    # N дней: включая сегодня, с 00:00 (MSK) дня (today - (N-1)).
    days_span = max(1, int(days))
    since_msk = today_start_msk - timedelta(days=days_span - 1)
    since = since_msk.astimezone(UTC).replace(tzinfo=None)

    total_users = int(
        await session.scalar(select(func.count(MonitorUserModel.id))) or 0
    )
    new_users = int(
        await session.scalar(
            select(func.count(MonitorUserModel.id)).where(
                MonitorUserModel.created_at >= since
            )
        )
        or 0
    )
    pro_users = int(
        await session.scalar(
            select(func.count(MonitorUserModel.id)).where(
                MonitorUserModel.plan == "pro",
                or_(
                    MonitorUserModel.pro_expires_at.is_(None),
                    MonitorUserModel.pro_expires_at >= now,
                ),
            )
        )
        or 0
    )

    total_tracks = int(
        await session.scalar(
            select(func.count(TrackModel.id)).where(TrackModel.is_deleted.is_(False))
        )
        or 0
    )
    active_tracks = int(
        await session.scalar(
            select(func.count(TrackModel.id)).where(
                TrackModel.is_deleted.is_(False),
                TrackModel.is_active.is_(True),
            )
        )
        or 0
    )
    new_tracks = int(
        await session.scalar(
            select(func.count(TrackModel.id)).where(
                TrackModel.is_deleted.is_(False),
                TrackModel.created_at >= since,
            )
        )
        or 0
    )

    checks_count = int(
        await session.scalar(
            select(func.count(SnapshotModel.id)).where(
                SnapshotModel.fetched_at >= since
            )
        )
        or 0
    )
    alerts_count = int(
        await session.scalar(
            select(func.count(AlertLogModel.id)).where(AlertLogModel.sent_at >= since)
        )
        or 0
    )

    cheap_scans_count = int(
        await session.scalar(
            select(func.count(AlertLogModel.id)).where(
                AlertLogModel.sent_at >= since,
                AlertLogModel.event_type == "cheap_scan",
            )
        )
        or 0
    )
    reviews_scans_count = int(
        await session.scalar(
            select(func.count(AlertLogModel.id)).where(
                AlertLogModel.sent_at >= since,
                AlertLogModel.event_type == "reviews_scan",
            )
        )
        or 0
    )

    return AdminStats(
        days=days,
        total_users=total_users,
        new_users=new_users,
        pro_users=pro_users,
        total_tracks=total_tracks,
        active_tracks=active_tracks,
        new_tracks=new_tracks,
        checks_count=checks_count,
        alerts_count=alerts_count,
        cheap_scans_count=cheap_scans_count,
        reviews_scans_count=reviews_scans_count,
    )
