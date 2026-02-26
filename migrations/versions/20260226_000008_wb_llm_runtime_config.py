"""
wb runtime config review sample limit.

Revision ID: 20260226_000008
Revises: 20260226_000007
Create Date: 2026-02-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260226_000008"
down_revision: str | None = "20260226_000007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_runtime_config "
        "ADD COLUMN IF NOT EXISTS review_sample_limit_per_side integer NOT NULL DEFAULT 50"
    )
    op.execute(
        "ALTER TABLE monitor_runtime_config "
        "ADD COLUMN IF NOT EXISTS analysis_model varchar(128) NOT NULL DEFAULT 'qwen/qwen3-32b'"
    )

    op.execute(
        "UPDATE monitor_runtime_config "
        "SET review_sample_limit_per_side = COALESCE(review_sample_limit_per_side, 50), "
        "analysis_model = COALESCE(NULLIF(analysis_model, ''), 'qwen/qwen3-32b')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE monitor_runtime_config DROP COLUMN IF EXISTS analysis_model"
    )
    op.execute(
        "ALTER TABLE monitor_runtime_config DROP COLUMN IF EXISTS review_sample_limit_per_side"
    )
