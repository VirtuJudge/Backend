"""add displayed name to user

Revision ID: edf3ff11ceff
Revises: a1b2c3d4e5f6
Create Date: 2026-09-07 18:40:22.562427
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "edf3ff11ceff"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name")
