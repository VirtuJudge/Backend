"""add project versions and erasure requests

Revision ID: a1b2c3d4e5f6
Revises: 9d6a1b2c3e4f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9d6a1b2c3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column("version", server_default=None)
    op.create_table(
        "project_erasure_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "idempotency_key",
            name="uq_project_erasure_request_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("project_erasure_requests")
    op.drop_column("projects", "version")
