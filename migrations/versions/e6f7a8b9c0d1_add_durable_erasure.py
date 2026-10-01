"""Add durable retention and erasure workflows.

Revision ID: e6f7a8b9c0d1
Revises: a4f6c8e1d2b3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "a4f6c8e1d2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("access_revoked_at", sa.DateTime(timezone=True)))
    op.add_column("practice_sessions", sa.Column("access_revoked_at", sa.DateTime(timezone=True)))
    op.create_table(
        "erasure_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid()),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("inventory", sa.JSON(), nullable=False),
        sa.UniqueConstraint("scope", "scope_id", name="uq_erasure_target"),
    )
    op.create_index("ix_erasure_requests_team_id", "erasure_requests", ["team_id"])
    op.create_index("ix_erasure_requests_next_attempt_at", "erasure_requests", ["next_attempt_at"])
    op.create_table(
        "erasure_steps",
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("erasure_requests.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("store", sa.String(30), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("deleted_records", sa.Integer(), nullable=False),
        sa.Column("deleted_objects", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(50)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "erasure_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("erasure_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("store", sa.String(30), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
    )
    op.create_index("ix_erasure_items_request_id", "erasure_items", ["request_id"])
    op.create_index("uq_erasure_item", "erasure_items", ["request_id", "storage_key"], unique=True)
    with op.batch_alter_table("ai_jobs") as batch:
        batch.alter_column("attempt_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("practice_session_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("erasure_request_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_ai_jobs_erasure_request_id", "erasure_requests", ["erasure_request_id"], ["id"]
        )
        batch.create_unique_constraint("uq_ai_jobs_erasure_request_id", ["erasure_request_id"])
        batch.create_check_constraint(
            "ck_ai_jobs_scope_ancestry",
            "(job_type = 'erase_ai_data' AND erasure_request_id IS NOT NULL "
            "AND attempt_id IS NULL AND practice_session_id IS NULL) OR "
            "(job_type <> 'erase_ai_data' AND erasure_request_id IS NULL "
            "AND attempt_id IS NOT NULL AND practice_session_id IS NOT NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM ai_jobs WHERE job_type = 'erase_ai_data'"))
    with op.batch_alter_table("ai_jobs") as batch:
        batch.drop_constraint("ck_ai_jobs_scope_ancestry", type_="check")
        batch.drop_constraint("uq_ai_jobs_erasure_request_id", type_="unique")
        batch.drop_constraint("fk_ai_jobs_erasure_request_id", type_="foreignkey")
        batch.drop_column("erasure_request_id")
        batch.alter_column("attempt_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("practice_session_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("erasure_items")
    op.drop_table("erasure_steps")
    op.drop_table("erasure_requests")
    op.drop_column("practice_sessions", "access_revoked_at")
    op.drop_column("projects", "access_revoked_at")
