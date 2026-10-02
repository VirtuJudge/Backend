"""Scope invitation resend idempotency.

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c3d4e5f6a7b"
down_revision: str | Sequence[str] | None = "1b2c3d4e5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM invitation_resend_idempotency"))
    with op.batch_alter_table("invitation_resend_idempotency") as batch_op:
        batch_op.drop_constraint("uq_invitation_resend_idempotency_key", type_="unique")
        batch_op.add_column(sa.Column("actor_id", sa.Uuid(), nullable=False))
        batch_op.add_column(sa.Column("team_id", sa.Uuid(), nullable=False))
        batch_op.add_column(sa.Column("operation", sa.String(50), nullable=False))
        batch_op.add_column(sa.Column("request_hash", sa.String(64), nullable=False))
        batch_op.create_foreign_key(
            "fk_invitation_resend_actor", "users", ["actor_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_foreign_key(
            "fk_invitation_resend_team", "teams", ["team_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_unique_constraint(
            "uq_invitation_resend_idempotency_scope",
            ["actor_id", "team_id", "invitation_id", "operation", "key"],
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM invitation_resend_idempotency"))
    with op.batch_alter_table("invitation_resend_idempotency") as batch_op:
        batch_op.drop_constraint("uq_invitation_resend_idempotency_scope", type_="unique")
        batch_op.drop_constraint("fk_invitation_resend_actor", type_="foreignkey")
        batch_op.drop_constraint("fk_invitation_resend_team", type_="foreignkey")
        for column in ("actor_id", "team_id", "operation", "request_hash"):
            batch_op.drop_column(column)
        batch_op.create_unique_constraint("uq_invitation_resend_idempotency_key", ["key"])
