"""
wb monitor tables.

Revision ID: 20260218_000001
Revises: 000000000000
Create Date: 2026-02-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260218_000001"
down_revision: str | None = "000000000000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("plan", sa.String(length=16), server_default="free", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_monitor_users_tg_user_id"), "monitor_users", ["tg_user_id"], unique=True)

    op.create_table(
        "monitor_tracks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("wb_item_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("target_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_drop_percent", sa.Integer(), nullable=True),
        sa.Column("watch_stock", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("watch_sizes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("check_interval_min", sa.Integer(), server_default="360", nullable=False),
        sa.Column("last_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("last_in_stock", sa.Boolean(), nullable=True),
        sa.Column("last_sizes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["monitor_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_monitor_tracks_user_id"), "monitor_tracks", ["user_id"], unique=False)
    op.create_index(op.f("ix_monitor_tracks_wb_item_id"), "monitor_tracks", ["wb_item_id"], unique=False)

    op.create_table(
        "monitor_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("price_current", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        sa.Column("sizes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["monitor_tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_monitor_snapshots_fetched_at"), "monitor_snapshots", ["fetched_at"], unique=False)
    op.create_index(op.f("ix_monitor_snapshots_track_id"), "monitor_snapshots", ["track_id"], unique=False)

    op.create_table(
        "monitor_alerts_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_hash", sa.String(length=128), nullable=False),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["monitor_tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash", name="uq_monitor_alert_event_hash"),
    )
    op.create_index(op.f("ix_monitor_alerts_log_event_type"), "monitor_alerts_log", ["event_type"], unique=False)
    op.create_index(op.f("ix_monitor_alerts_log_sent_at"), "monitor_alerts_log", ["sent_at"], unique=False)
    op.create_index(op.f("ix_monitor_alerts_log_track_id"), "monitor_alerts_log", ["track_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_monitor_alerts_log_track_id"), table_name="monitor_alerts_log")
    op.drop_index(op.f("ix_monitor_alerts_log_sent_at"), table_name="monitor_alerts_log")
    op.drop_index(op.f("ix_monitor_alerts_log_event_type"), table_name="monitor_alerts_log")
    op.drop_table("monitor_alerts_log")

    op.drop_index(op.f("ix_monitor_snapshots_track_id"), table_name="monitor_snapshots")
    op.drop_index(op.f("ix_monitor_snapshots_fetched_at"), table_name="monitor_snapshots")
    op.drop_table("monitor_snapshots")

    op.drop_index(op.f("ix_monitor_tracks_wb_item_id"), table_name="monitor_tracks")
    op.drop_index(op.f("ix_monitor_tracks_user_id"), table_name="monitor_tracks")
    op.drop_table("monitor_tracks")

    op.drop_index(op.f("ix_monitor_users_tg_user_id"), table_name="monitor_users")
    op.drop_table("monitor_users")
