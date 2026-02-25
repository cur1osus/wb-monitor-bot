"""
wb rating and reviews fields.

Revision ID: 20260225_000005
Revises: 20260225_000004
Create Date: 2026-02-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260225_000005"
down_revision: str | None = "20260225_000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS last_rating numeric(3,2) NULL"
    )
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS last_reviews integer NULL"
    )
    op.execute(
        "ALTER TABLE monitor_snapshots ADD COLUMN IF NOT EXISTS rating_current numeric(3,2) NULL"
    )
    op.execute(
        "ALTER TABLE monitor_snapshots ADD COLUMN IF NOT EXISTS reviews_current integer NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE monitor_snapshots DROP COLUMN IF EXISTS reviews_current")
    op.execute("ALTER TABLE monitor_snapshots DROP COLUMN IF EXISTS rating_current")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS last_reviews")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS last_rating")
