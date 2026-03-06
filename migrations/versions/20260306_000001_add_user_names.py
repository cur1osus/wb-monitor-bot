"""add first_name and last_name to monitor_users

Revision ID: 20260306_000001
Revises: 20260303_000001
Create Date: 2026-03-06 19:45:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260306_000001"
down_revision: Union[str, None] = "20260303_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitor_users", sa.Column("first_name", sa.String(length=255), nullable=True))
    op.add_column("monitor_users", sa.Column("last_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("monitor_users", "last_name")
    op.drop_column("monitor_users", "first_name")
