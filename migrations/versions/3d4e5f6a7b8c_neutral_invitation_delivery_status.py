"""Use a provider-neutral invitation delivery status.

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3d4e5f6a7b8c"
down_revision: str | Sequence[str] | None = "2c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE deliverystatus RENAME VALUE 'accepted_by_gmail' TO 'accepted_by_provider'"
        )
    else:
        op.execute(
            sa.text(
                "UPDATE team_invitations SET delivery_status = 'accepted_by_provider' "
                "WHERE delivery_status = 'accepted_by_gmail'"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE deliverystatus RENAME VALUE 'accepted_by_provider' TO 'accepted_by_gmail'"
        )
    else:
        op.execute(
            sa.text(
                "UPDATE team_invitations SET delivery_status = 'accepted_by_gmail' "
                "WHERE delivery_status = 'accepted_by_provider'"
            )
        )
