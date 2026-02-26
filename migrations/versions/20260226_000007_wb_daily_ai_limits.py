"""
wb runtime config daily ai limits.

Revision ID: 20260226_000007
Revises: 20260225_000006
Create Date: 2026-02-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260226_000007"
down_revision: str | None = "20260225_000006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_runtime_config "
        "ADD COLUMN IF NOT EXISTS free_daily_ai_limit integer NOT NULL DEFAULT 3"
    )
    op.execute(
        "ALTER TABLE monitor_runtime_config "
        "ADD COLUMN IF NOT EXISTS pro_daily_ai_limit integer NOT NULL DEFAULT 10"
    )

    op.execute(
        "UPDATE monitor_runtime_config "
        "SET free_daily_ai_limit = COALESCE(free_daily_ai_limit, 3), "
        "pro_daily_ai_limit = COALESCE(pro_daily_ai_limit, 10)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_runtime_config DROP COLUMN IF EXISTS pro_daily_ai_limit"
    )
    op.execute(
        "ALTER TABLE monitor_runtime_config DROP COLUMN IF EXISTS free_daily_ai_limit"
    )
