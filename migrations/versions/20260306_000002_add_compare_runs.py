"""add compare runs history table

Revision ID: 20260306_000002
Revises: 20260306_000001
Create Date: 2026-03-06 21:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260306_000002"
down_revision: Union[str, None] = "20260306_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitor_compare_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="balanced"),
        sa.Column("input_item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("winner_item_id", sa.BigInteger(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["monitor_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_monitor_compare_runs_user_id"), "monitor_compare_runs", ["user_id"], unique=False)
    op.create_index(op.f("ix_monitor_compare_runs_mode"), "monitor_compare_runs", ["mode"], unique=False)
    op.create_index(op.f("ix_monitor_compare_runs_created_at"), "monitor_compare_runs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_monitor_compare_runs_created_at"), table_name="monitor_compare_runs")
    op.drop_index(op.f("ix_monitor_compare_runs_mode"), table_name="monitor_compare_runs")
    op.drop_index(op.f("ix_monitor_compare_runs_user_id"), table_name="monitor_compare_runs")
    op.drop_table("monitor_compare_runs")
