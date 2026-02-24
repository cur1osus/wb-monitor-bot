"""
wb referrals.

Revision ID: 20260219_000002
Revises: 20260218_000001
Create Date: 2026-02-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260219_000002"
down_revision: str | None = "20260218_000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitor_users", sa.Column("referred_by_tg_user_id", sa.BigInteger(), nullable=True))
    op.add_column("monitor_users", sa.Column("referral_bonus_granted_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_monitor_users_referred_by_tg_user_id"),
        "monitor_users",
        ["referred_by_tg_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_monitor_users_referred_by_tg_user_id"), table_name="monitor_users")
    op.drop_column("monitor_users", "referral_bonus_granted_at")
    op.drop_column("monitor_users", "referred_by_tg_user_id")
