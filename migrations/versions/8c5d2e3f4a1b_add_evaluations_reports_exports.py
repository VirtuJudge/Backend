"""add evaluations reports and report_exports

Revision ID: 8c5d2e3f4a1b
Revises: 7b4e1a6d2c8f
Create Date: 2026-09-15 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c5d2e3f4a1b"
down_revision: str | Sequence[str] | None = "7b4e1a6d2c8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "evaluations" not in tables:
        op.create_table(
            "evaluations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("practice_session_id", sa.Uuid(), nullable=False),
            sa.Column("analysis_attempt_id", sa.Uuid(), nullable=False),
            sa.Column("qa_round_id", sa.Uuid(), nullable=False),
            sa.Column("rubric_id", sa.String(100), nullable=False),
            sa.Column("rubric_version", sa.Integer(), nullable=False),
            sa.Column("overall_score", sa.Float(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["practice_session_id"], ["practice_sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["analysis_attempt_id"], ["analysis_attempts.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["qa_round_id"], ["qa_rounds.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_evaluations_practice_session_id", "evaluations", ["practice_session_id"]
        )
        op.create_index(
            "ix_evaluations_analysis_attempt_id", "evaluations", ["analysis_attempt_id"]
        )
        op.create_index(
            "ix_evaluations_qa_round_id", "evaluations", ["qa_round_id"]
        )

    if "reports" not in tables:
        op.create_table(
            "reports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("practice_session_id", sa.Uuid(), nullable=False),
            sa.Column("evaluation_id", sa.Uuid(), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("executive_summary", sa.Text(), nullable=False),
            sa.Column("overall_score", sa.Float(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["practice_session_id"], ["practice_sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_id"], ["evaluations.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("practice_session_id", name="uq_reports_practice_session_id"),
            sa.UniqueConstraint("evaluation_id", name="uq_reports_evaluation_id"),
        )
        op.create_index(
            "ix_reports_practice_session_id", "reports", ["practice_session_id"]
        )

    if "report_exports" not in tables:
        op.create_table(
            "report_exports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("report_id", sa.Uuid(), nullable=False),
            sa.Column("practice_session_id", sa.Uuid(), nullable=False),
            sa.Column("format", sa.String(20), server_default="pdf", nullable=False),
            sa.Column("status", sa.String(50), server_default="queued", nullable=False),
            sa.Column("asset_version_id", sa.Uuid(), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["practice_session_id"], ["practice_sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["asset_version_id"], ["asset_versions.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_report_exports_report_id", "report_exports", ["report_id"]
        )
        op.create_index(
            "ix_report_exports_practice_session_id", "report_exports", ["practice_session_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "report_exports" in tables:
        op.drop_table("report_exports")
    if "reports" in tables:
        op.drop_table("reports")
    if "evaluations" in tables:
        op.drop_table("evaluations")
