from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


class MonitorUserModel(Base):
    __tablename__ = "monitor_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(String(16), default="free")
    pro_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    referral_code: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )
    referred_by_tg_user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    referral_bonus_granted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    tracks: Mapped[list[TrackModel]] = relationship(
        back_populates="user", cascade="all,delete-orphan"
    )


class RuntimeConfigModel(Base):
    __tablename__ = "monitor_runtime_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    free_interval_min: Mapped[int] = mapped_column(Integer, default=360)
    pro_interval_min: Mapped[int] = mapped_column(Integer, default=60)
    cheap_match_percent: Mapped[int] = mapped_column(Integer, default=50)
    free_daily_ai_limit: Mapped[int] = mapped_column(Integer, default=3)
    pro_daily_ai_limit: Mapped[int] = mapped_column(Integer, default=10)
    review_sample_limit_per_side: Mapped[int] = mapped_column(Integer, default=50)
    analysis_model: Mapped[str] = mapped_column(String(128), default="qwen/qwen3-32b")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class TrackModel(Base):
    __tablename__ = "monitor_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_users.id", ondelete="CASCADE"), index=True
    )
    wb_item_id: Mapped[int] = mapped_column(BigInteger, index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)

    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    target_drop_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)

    watch_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    watch_qty: Mapped[bool] = mapped_column(Boolean, default=False)
    watch_sizes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_interval_min: Mapped[int] = mapped_column(Integer, default=360)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    last_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    last_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_in_stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sizes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    user: Mapped[MonitorUserModel] = relationship(back_populates="tracks")
    snapshots: Mapped[list[SnapshotModel]] = relationship(
        back_populates="track", cascade="all,delete-orphan"
    )


class SnapshotModel(Base):
    __tablename__ = "monitor_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_tracks.id", ondelete="CASCADE"), index=True
    )

    price_current: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    rating_current: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    reviews_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean)
    sizes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    qty_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )

    track: Mapped[TrackModel] = relationship(back_populates="snapshots")


class AlertLogModel(Base):
    __tablename__ = "monitor_alerts_log"
    __table_args__ = (
        UniqueConstraint("event_hash", name="uq_monitor_alert_event_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_tracks.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    event_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class ReferralRewardModel(Base):
    __tablename__ = "monitor_referral_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_users.id", ondelete="CASCADE"), index=True
    )
    invited_user_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_users.id", ondelete="CASCADE"), index=True
    )
    invited_tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    payment_charge_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    rewarded_days: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )
