"""Scope invitation creation idempotency.

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b2c3d4e5f6a"
down_revision: str | Sequence[str] | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("team_invitations") as batch_op:
        batch_op.drop_constraint("uq_team_invitations_idempotency_key", type_="unique")
    op.create_table(
        "invitation_creation_idempotency",
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("operation", sa.String(50), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "invitation_id",
            sa.Uuid(),
            sa.ForeignKey("team_invitations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT idempotency_key FROM team_invitations "
                "GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise ValueError("Cannot restore global invitation keys while scoped duplicates exist")
    op.drop_table("invitation_creation_idempotency")
    with op.batch_alter_table("team_invitations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_team_invitations_idempotency_key", ["idempotency_key"]
        )
