"""Add scoped project creation idempotency.

Revision ID: 0a1b2c3d4e5f
Revises: f7a8b9c0d1e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_creation_idempotency",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("operation", sa.String(50), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("project_creation_idempotency")
