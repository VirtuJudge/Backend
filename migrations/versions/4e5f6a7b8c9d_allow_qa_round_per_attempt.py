"""Keep Q&A history when retrying a failed Analysis Attempt."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e5f6a7b8c9d"
down_revision: str | Sequence[str] | None = "3d4e5f6a7b8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("qa_rounds")
    constraint = next(c for c in constraints if c["column_names"] == ["practice_session_id"])
    naming = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("qa_rounds", naming_convention=naming) as batch:
        batch.drop_constraint(
            constraint["name"] or "uq_qa_rounds_practice_session_id", type_="unique"
        )
        batch.create_index("ix_qa_rounds_practice_session_id", ["practice_session_id"])
    op.execute(
        sa.text(
            "UPDATE practice_sessions SET status = 'failed', version = version + 1, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE status IN ('analyzing', 'questions_in_progress', 'report_generating') "
            "AND EXISTS (SELECT 1 FROM analysis_attempts a "
            "WHERE a.session_id = practice_sessions.id AND a.status = 'failed' "
            "AND NOT EXISTS (SELECT 1 FROM analysis_attempts newer "
            "WHERE newer.session_id = a.session_id AND newer.attempt_number > a.attempt_number))"
        )
    )


def downgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT practice_session_id FROM qa_rounds "
                "GROUP BY practice_session_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates is not None:
        raise RuntimeError("Multiple Q&A Rounds exist; preserve history and use a forward fix.")
    with op.batch_alter_table("qa_rounds") as batch:
        batch.drop_index("ix_qa_rounds_practice_session_id")
        batch.create_unique_constraint("qa_rounds_practice_session_id_key", ["practice_session_id"])
