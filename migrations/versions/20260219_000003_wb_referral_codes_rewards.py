"""
wb referral codes and rewards.

Revision ID: 20260219_000003
Revises: 20260219_000002
Create Date: 2026-02-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260219_000003"
down_revision: str | None = "20260219_000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitor_users", sa.Column("referral_code", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_monitor_users_referral_code"), "monitor_users", ["referral_code"], unique=True)

    op.create_table(
        "monitor_referral_rewards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("referrer_user_id", sa.Integer(), nullable=False),
        sa.Column("invited_user_id", sa.Integer(), nullable=False),
        sa.Column("invited_tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_charge_id", sa.String(length=255), nullable=False),
        sa.Column("rewarded_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["monitor_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_user_id"], ["monitor_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_monitor_referral_rewards_referrer_user_id"), "monitor_referral_rewards", ["referrer_user_id"], unique=False)
    op.create_index(op.f("ix_monitor_referral_rewards_invited_user_id"), "monitor_referral_rewards", ["invited_user_id"], unique=False)
    op.create_index(op.f("ix_monitor_referral_rewards_invited_tg_user_id"), "monitor_referral_rewards", ["invited_tg_user_id"], unique=False)
    op.create_index(op.f("ix_monitor_referral_rewards_payment_charge_id"), "monitor_referral_rewards", ["payment_charge_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_monitor_referral_rewards_payment_charge_id"), table_name="monitor_referral_rewards")
    op.drop_index(op.f("ix_monitor_referral_rewards_invited_tg_user_id"), table_name="monitor_referral_rewards")
    op.drop_index(op.f("ix_monitor_referral_rewards_invited_user_id"), table_name="monitor_referral_rewards")
    op.drop_index(op.f("ix_monitor_referral_rewards_referrer_user_id"), table_name="monitor_referral_rewards")
    op.drop_table("monitor_referral_rewards")

    op.drop_index(op.f("ix_monitor_users_referral_code"), table_name="monitor_users")
    op.drop_column("monitor_users", "referral_code")
