"""add Q&A backend flow

Revision ID: 3f9a7c2d1e6b
Revises: f31a1f55d085
Create Date: 2026-09-14 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f9a7c2d1e6b"
down_revision: str | Sequence[str] | None = "f31a1f55d085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("practice_session_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_attempt_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.Enum("not_started", "in_progress", "completed", name="qaroundstate"),
            nullable=False,
        ),
        sa.Column("current_question_id", sa.Uuid(), nullable=True),
        sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_attempt_id"], ["analysis_attempts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["practice_session_id"], ["practice_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_attempt_id"),
        sa.UniqueConstraint("practice_session_id"),
    )
    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("qa_round_id", sa.Uuid(), nullable=False),
        sa.Column("practice_session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Enum("primary", "follow_up", name="questionkind"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("rubric_dimension", sa.String(length=100), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("parent_answer_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state",
            sa.Enum("pending", "active", "answered", "skipped", name="questionstate"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["practice_session_id"], ["practice_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["qa_round_id"], ["qa_rounds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qa_round_id", "position", name="uq_question_position"),
    )
    op.create_index("ix_questions_qa_round_id", "questions", ["qa_round_id"])
    op.create_index("ix_questions_practice_session_id", "questions", ["practice_session_id"])
    op.create_table(
        "answers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("qa_round_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("answered_by", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.Enum("draft", "submitted", "skipped", name="answerstatus"), nullable=False
        ),
        sa.Column("audio_asset_version_id", sa.Uuid(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("transcript_artifact_id", sa.String(length=255), nullable=True),
        sa.Column("assessment_artifact_id", sa.String(length=255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["answered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["audio_asset_version_id"], ["asset_versions.id"]),
        sa.ForeignKeyConstraint(["qa_round_id"], ["qa_rounds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_id"),
    )
    op.create_index("ix_answers_qa_round_id", "answers", ["qa_round_id"])
    op.create_index(
        "ix_answers_idempotency", "answers", ["question_id", "idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_answers_idempotency", table_name="answers")
    op.drop_index("ix_answers_qa_round_id", table_name="answers")
    op.drop_table("answers")
    op.drop_index("ix_questions_practice_session_id", table_name="questions")
    op.drop_index("ix_questions_qa_round_id", table_name="questions")
    op.drop_table("questions")
    op.drop_table("qa_rounds")
    bind = op.get_bind()
    sa.Enum(name="answerstatus").drop(bind, checkfirst=True)
    sa.Enum(name="questionstate").drop(bind, checkfirst=True)
    sa.Enum(name="questionkind").drop(bind, checkfirst=True)
    sa.Enum(name="qaroundstate").drop(bind, checkfirst=True)
