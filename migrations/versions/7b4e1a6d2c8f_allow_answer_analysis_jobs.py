"""allow answer analysis jobs

Revision ID: 7b4e1a6d2c8f
Revises: 3f9a7c2d1e6b
Create Date: 2026-09-14 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b4e1a6d2c8f"
down_revision: str | Sequence[str] | None = "3f9a7c2d1e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "ai_jobs", naming_convention=naming_convention if bind.dialect.name == "sqlite" else None
    ) as batch_op:
        constraint_name = (
            "uq_ai_jobs_attempt_id"
            if bind.dialect.name == "sqlite"
            else "analysis_jobs_attempt_id_key"
        )
        batch_op.drop_constraint(constraint_name, type_="unique")
        batch_op.add_column(sa.Column("answer_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_ai_jobs_answer_id_answers", "answers", ["answer_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_unique_constraint("uq_ai_jobs_answer_id", ["answer_id"])
    op.create_index(
        "uq_ai_jobs_session_attempt",
        "ai_jobs",
        ["attempt_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'analyze_session'"),
        sqlite_where=sa.text("job_type = 'analyze_session'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("uq_ai_jobs_session_attempt", table_name="ai_jobs")
    with op.batch_alter_table("ai_jobs") as batch_op:
        batch_op.drop_constraint("uq_ai_jobs_answer_id", type_="unique")
        batch_op.drop_constraint("fk_ai_jobs_answer_id_answers", type_="foreignkey")
        batch_op.drop_column("answer_id")
        batch_op.create_unique_constraint(
            "uq_ai_jobs_attempt_id"
            if bind.dialect.name == "sqlite"
            else "analysis_jobs_attempt_id_key",
            ["attempt_id"],
        )
