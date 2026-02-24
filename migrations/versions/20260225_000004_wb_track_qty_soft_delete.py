"""
wb track qty and soft delete fields.

Revision ID: 20260225_000004
Revises: 20260219_000003
Create Date: 2026-02-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260225_000004"
down_revision: str | None = "20260219_000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS watch_qty boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS is_deleted boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS deleted_at timestamp NULL"
    )
    op.execute(
        "ALTER TABLE monitor_tracks ADD COLUMN IF NOT EXISTS last_qty integer NULL"
    )
    op.execute(
        "ALTER TABLE monitor_snapshots ADD COLUMN IF NOT EXISTS qty_current integer NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE monitor_snapshots DROP COLUMN IF EXISTS qty_current")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS last_qty")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS is_deleted")
    op.execute("ALTER TABLE monitor_tracks DROP COLUMN IF EXISTS watch_qty")
