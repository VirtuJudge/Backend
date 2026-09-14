"""rename analysis_jobs to ai_jobs

Revision ID: 40d664ee8b4d
Revises: ef143c2a901b
Create Date: 2026-09-14 01:50:35.692655
"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "40d664ee8b4d"
down_revision: str | Sequence[str] | None = "ef143c2a901b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "analysis_jobs" in tables and "ai_jobs" not in tables:
        op.rename_table("analysis_jobs", "ai_jobs")
        if bind.dialect.name == "postgresql":
            with contextlib.suppress(Exception):
                op.execute(
                    "ALTER INDEX ix_analysis_jobs_correlation_id "
                    "RENAME TO ix_ai_jobs_correlation_id"
                )
            with contextlib.suppress(Exception):
                op.execute(
                    "ALTER INDEX ix_analysis_jobs_practice_session_id "
                    "RENAME TO ix_ai_jobs_practice_session_id"
                )
        else:
            with contextlib.suppress(Exception):
                op.drop_index("ix_analysis_jobs_correlation_id", table_name="ai_jobs")
            op.create_index("ix_ai_jobs_correlation_id", "ai_jobs", ["correlation_id"])
            with contextlib.suppress(Exception):
                op.drop_index("ix_analysis_jobs_practice_session_id", table_name="ai_jobs")
            op.create_index("ix_ai_jobs_practice_session_id", "ai_jobs", ["practice_session_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER INDEX ix_ai_jobs_correlation_id RENAME TO ix_analysis_jobs_correlation_id"
        )
        op.execute(
            "ALTER INDEX ix_ai_jobs_practice_session_id "
            "RENAME TO ix_analysis_jobs_practice_session_id"
        )
    else:
        op.drop_index("ix_ai_jobs_correlation_id", table_name="ai_jobs")
        op.create_index("ix_analysis_jobs_correlation_id", "ai_jobs", ["correlation_id"])
        op.drop_index("ix_ai_jobs_practice_session_id", table_name="ai_jobs")
        op.create_index("ix_analysis_jobs_practice_session_id", "ai_jobs", ["practice_session_id"])
    op.rename_table("ai_jobs", "analysis_jobs")
