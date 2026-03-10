"""add next_check_at and worker-oriented indexes."""

from alembic import op
import sqlalchemy as sa


revision = "20260309_000001"
down_revision = "20260308_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monitor_tracks",
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
    )

    op.execute(
        """
        UPDATE monitor_tracks
        SET next_check_at = COALESCE(last_checked_at, NOW() AT TIME ZONE 'UTC')
            + (check_interval_min * INTERVAL '1 minute')
        WHERE next_check_at IS NULL
        """
    )

    op.create_index(
        "ix_monitor_users_plan_expires",
        "monitor_users",
        ["plan", "pro_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_monitor_tracks_active_due",
        "monitor_tracks",
        ["is_active", "is_deleted", "next_check_at"],
        unique=False,
    )
    op.create_index(
        "ix_monitor_snapshots_track_fetched",
        "monitor_snapshots",
        ["track_id", "fetched_at"],
        unique=False,
    )
    op.create_index(
        "ix_monitor_alerts_track_sent",
        "monitor_alerts_log",
        ["track_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "ix_monitor_feature_usage_lookup",
        "monitor_feature_usage",
        ["tg_user_id", "feature", "period", "window_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_monitor_feature_usage_lookup", table_name="monitor_feature_usage")
    op.drop_index("ix_monitor_alerts_track_sent", table_name="monitor_alerts_log")
    op.drop_index("ix_monitor_snapshots_track_fetched", table_name="monitor_snapshots")
    op.drop_index("ix_monitor_tracks_active_due", table_name="monitor_tracks")
    op.drop_index("ix_monitor_users_plan_expires", table_name="monitor_users")
    op.drop_column("monitor_tracks", "next_check_at")
