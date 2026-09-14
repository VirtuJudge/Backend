from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.infrastructure.database import Base


class QARoundModel(Base):
    __tablename__ = "qa_rounds"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    practice_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("practice_sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    analysis_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_attempts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    state: Mapped[QARoundState] = mapped_column(
        Enum(QARoundState, values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    current_question_id: Mapped[UUID | None] = mapped_column(nullable=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionModel(Base):
    __tablename__ = "questions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    qa_round_id: Mapped[UUID] = mapped_column(
        ForeignKey("qa_rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    practice_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[QuestionKind] = mapped_column(
        Enum(QuestionKind, values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    rubric_dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    parent_answer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    state: Mapped[QuestionState] = mapped_column(
        Enum(QuestionState, values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("qa_round_id", "position", name="uq_question_position"),)


class AnswerModel(Base):
    __tablename__ = "answers"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    qa_round_id: Mapped[UUID] = mapped_column(
        ForeignKey("qa_rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    answered_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[AnswerStatus] = mapped_column(
        Enum(AnswerStatus, values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    audio_asset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_versions.id"), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript_artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assessment_artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_answers_idempotency", "question_id", "idempotency_key", unique=True),
    )
