"""telegram group fk — move org link FK from telegram_groups to organizations

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop telegram_chat from organizations
    op.drop_column("organizations", "telegram_chat")

    # Add telegram_group_id FK to organizations
    op.add_column(
        "organizations",
        sa.Column(
            "telegram_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_org_telegram_group_id",
        "organizations",
        ["telegram_group_id"],
    )
    op.create_foreign_key(
        "fk_org_telegram_group_id",
        "organizations",
        "telegram_groups",
        ["telegram_group_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_org_telegram_group_id", "organizations", type_="foreignkey")
    op.drop_constraint("uq_org_telegram_group_id", "organizations", type_="unique")
    op.drop_column("organizations", "telegram_group_id")
    op.add_column(
        "organizations",
        sa.Column("telegram_chat", sa.String(255), nullable=True),
    )
