"""
wb runtime config.

Revision ID: 20260225_000006
Revises: 20260225_000005
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260225_000006"
down_revision: str | None = "20260225_000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_runtime_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "free_interval_min", sa.Integer(), nullable=False, server_default="360"
        ),
        sa.Column(
            "pro_interval_min", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column(
            "cheap_match_percent", sa.Integer(), nullable=False, server_default="50"
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO monitor_runtime_config (id, free_interval_min, pro_interval_min, cheap_match_percent) VALUES (1, 360, 60, 50) ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("monitor_runtime_config")
