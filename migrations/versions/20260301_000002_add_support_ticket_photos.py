"""add support ticket photos

Revision ID: 20260301_000002
Revises: 20260301_000001
Create Date: 2026-03-01 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260301_000002"
down_revision: Union[str, None] = "20260301_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitor_support_ticket_photos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(length=500), nullable=False),
        sa.Column("file_unique_id", sa.String(length=100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["monitor_support_tickets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_monitor_support_ticket_photos_ticket_id"),
        "monitor_support_ticket_photos",
        ["ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_monitor_support_ticket_photos_ticket_id"),
        table_name="monitor_support_ticket_photos",
    )
    op.drop_table("monitor_support_ticket_photos")
