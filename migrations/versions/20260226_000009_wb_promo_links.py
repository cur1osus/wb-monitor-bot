"""
wb promo links.

Revision ID: 20260226_000009
Revises: 20260226_000008
Create Date: 2026-02-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260226_000009"
down_revision: str | None = "20260226_000008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_promo_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_by_tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_monitor_promo_links_code"),
        "monitor_promo_links",
        ["code"],
        unique=True,
    )
    op.create_index(
        op.f("ix_monitor_promo_links_kind"),
        "monitor_promo_links",
        ["kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_links_expires_at"),
        "monitor_promo_links",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_links_is_active"),
        "monitor_promo_links",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_links_created_by_tg_user_id"),
        "monitor_promo_links",
        ["created_by_tg_user_id"],
        unique=False,
    )

    op.create_table(
        "monitor_promo_activations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("promo_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("value_applied", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["promo_id"], ["monitor_promo_links.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["monitor_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "promo_id", "user_id", name="uq_monitor_promo_activation_user"
        ),
    )
    op.create_index(
        op.f("ix_monitor_promo_activations_promo_id"),
        "monitor_promo_activations",
        ["promo_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_activations_user_id"),
        "monitor_promo_activations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_activations_tg_user_id"),
        "monitor_promo_activations",
        ["tg_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_activations_consumed_at"),
        "monitor_promo_activations",
        ["consumed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_monitor_promo_activations_created_at"),
        "monitor_promo_activations",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_monitor_promo_activations_created_at"),
        table_name="monitor_promo_activations",
    )
    op.drop_index(
        op.f("ix_monitor_promo_activations_consumed_at"),
        table_name="monitor_promo_activations",
    )
    op.drop_index(
        op.f("ix_monitor_promo_activations_tg_user_id"),
        table_name="monitor_promo_activations",
    )
    op.drop_index(
        op.f("ix_monitor_promo_activations_user_id"),
        table_name="monitor_promo_activations",
    )
    op.drop_index(
        op.f("ix_monitor_promo_activations_promo_id"),
        table_name="monitor_promo_activations",
    )
    op.drop_table("monitor_promo_activations")

    op.drop_index(
        op.f("ix_monitor_promo_links_created_by_tg_user_id"),
        table_name="monitor_promo_links",
    )
    op.drop_index(
        op.f("ix_monitor_promo_links_is_active"),
        table_name="monitor_promo_links",
    )
    op.drop_index(
        op.f("ix_monitor_promo_links_expires_at"),
        table_name="monitor_promo_links",
    )
    op.drop_index(
        op.f("ix_monitor_promo_links_kind"),
        table_name="monitor_promo_links",
    )
    op.drop_index(
        op.f("ix_monitor_promo_links_code"),
        table_name="monitor_promo_links",
    )
    op.drop_table("monitor_promo_links")
