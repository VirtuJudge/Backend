"""Schedule one report job per Analysis Attempt."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f7a8b9c0d1e"
down_revision: str | Sequence[str] | None = "4e5f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT attempt_id FROM ai_jobs WHERE job_type = 'generate_report' "
                "GROUP BY attempt_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates is not None:
        raise RuntimeError(
            "Duplicate report jobs exist; preserve history and reconcile them before migrating."
        )
    op.create_index(
        "uq_ai_jobs_report_attempt",
        "ai_jobs",
        ["attempt_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'generate_report'"),
        sqlite_where=sa.text("job_type = 'generate_report'"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_jobs_report_attempt", table_name="ai_jobs")
