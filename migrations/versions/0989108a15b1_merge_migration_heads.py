"""merge migration heads

Revision ID: 0989108a15b1
Revises: b5af41259a43, d5e6f7a8b9c0
Create Date: 2026-09-09 05:12:49.117106
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0989108a15b1'
down_revision: str | Sequence[str] | None = ('b5af41259a43', 'd5e6f7a8b9c0')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
