"""add team creation idempotency records

Revision ID: 9d6a1b2c3e4f
Revises: 8005859f45dd
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d6a1b2c3e4f"
down_revision: str | Sequence[str] | None = "8005859f45dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("teams") as batch_op:
        batch_op.alter_column("version", server_default=None)
    op.create_table(
        "team_creation_idempotency",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("team_creation_idempotency")
    op.drop_column("teams", "version")
