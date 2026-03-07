"""add persistent feature usage table

Revision ID: 20260307_000003
Revises: 20260306_000002
Create Date: 2026-03-07 15:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260307_000003"
down_revision: Union[str, None] = "20260306_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitor_feature_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("window_key", sa.String(length=16), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tg_user_id",
            "feature",
            "period",
            "window_key",
            name="uq_monitor_feature_usage_window",
        ),
    )
    op.create_index(
        op.f("ix_monitor_feature_usage_tg_user_id"),
        "monitor_feature_usage",
        ["tg_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_feature_usage_feature"),
        "monitor_feature_usage",
        ["feature"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_feature_usage_period"),
        "monitor_feature_usage",
        ["period"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_feature_usage_window_key"),
        "monitor_feature_usage",
        ["window_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_monitor_feature_usage_window_key"), table_name="monitor_feature_usage")
    op.drop_index(op.f("ix_monitor_feature_usage_period"), table_name="monitor_feature_usage")
    op.drop_index(op.f("ix_monitor_feature_usage_feature"), table_name="monitor_feature_usage")
    op.drop_index(op.f("ix_monitor_feature_usage_tg_user_id"), table_name="monitor_feature_usage")
    op.drop_table("monitor_feature_usage")
