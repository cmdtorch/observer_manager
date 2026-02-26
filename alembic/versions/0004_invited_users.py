"""invited_users — recreate with UUID FK (dropped but not recreated in 0002)

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invited_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("grafana_invited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("grafana_invite_link", sa.String(512), nullable=True),
        sa.Column("glitchtip_invited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("glitchtip_invite_link", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("invited_users")
