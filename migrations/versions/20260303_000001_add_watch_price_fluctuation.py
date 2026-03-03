"""add watch_price_fluctuation, drop target_price and target_drop_percent

Revision ID: 20260303_000001
Revises: 20260301_000002
Create Date: 2026-03-03 22:54:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260303_000001"
down_revision: Union[str, None] = "20260301_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Добавить колонку для отслеживания колебаний цены
    op.add_column(
        "monitor_tracks",
        sa.Column(
            "watch_price_fluctuation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # Удалить устаревшие колонки целевой цены и процента падения
    op.drop_column("monitor_tracks", "target_price")
    op.drop_column("monitor_tracks", "target_drop_percent")


def downgrade() -> None:
    op.add_column(
        "monitor_tracks",
        sa.Column("target_drop_percent", sa.Integer(), nullable=True),
    )
    op.add_column(
        "monitor_tracks",
        sa.Column("target_price", sa.Numeric(12, 2), nullable=True),
    )
    op.drop_column("monitor_tracks", "watch_price_fluctuation")
