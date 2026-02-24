"""
wb monitor pro fields.

Revision ID: 20260218_000002
Revises: 20260218_000001
Create Date: 2026-02-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260218_000002"
down_revision: str | None = "20260218_000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitor_users", sa.Column("pro_expires_at", sa.DateTime(), nullable=True))
    op.add_column(
        "monitor_tracks",
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("monitor_tracks", "error_count")
    op.drop_column("monitor_users", "pro_expires_at")
