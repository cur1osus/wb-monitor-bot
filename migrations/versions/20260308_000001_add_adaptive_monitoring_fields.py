"""add_adaptive_monitoring_fields: price_change_count, last_price_changed_at."""

from alembic import op
import sqlalchemy as sa

revision = "20260308_000001"
down_revision = "20260307_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monitor_tracks",
        sa.Column("price_change_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "monitor_tracks",
        sa.Column("last_price_changed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monitor_tracks", "last_price_changed_at")
    op.drop_column("monitor_tracks", "price_change_count")
