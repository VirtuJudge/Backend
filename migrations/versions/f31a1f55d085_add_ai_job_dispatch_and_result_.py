"""add_ai_job_dispatch_and_result_persistence

Revision ID: f31a1f55d085
Revises: 40d664ee8b4d
Create Date: 2026-09-14 02:22:41.385693
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f31a1f55d085"
down_revision: str | Sequence[str] | None = "40d664ee8b4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_jobs") as batch_op:
        batch_op.add_column(sa.Column("payload", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "dispatch_retry_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("last_dispatch_error_category", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(sa.Column("completed_result", sa.JSON(), nullable=True))
        batch_op.create_index(
            "ix_ai_jobs_pending_dispatch",
            ["status", "next_dispatch_at", "created_at", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_jobs") as batch_op:
        batch_op.drop_index("ix_ai_jobs_pending_dispatch")
        batch_op.drop_column("completed_result")
        batch_op.drop_column("last_dispatch_error_category")
        batch_op.drop_column("dispatch_retry_count")
        batch_op.drop_column("next_dispatch_at")
        batch_op.drop_column("queued_at")
        batch_op.drop_column("payload")
