from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.infrastructure.database import Base


class AnalysisJobModel(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_attempts.id"),
        nullable=False,
    )

    answer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), nullable=True, unique=True
    )

    practice_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("practice_sessions.id"),
        nullable=False,
        index=True,
    )

    analysis_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    job_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="analyze_session",
    )

    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(
            AnalysisJobStatus,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="analysisjobstatus",
        ),
        nullable=False,
    )

    correlation_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
    )

    last_update_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    payload_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    next_dispatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    dispatch_retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_dispatch_error_category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    completed_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    result = synonym("completed_result")
    next_eligible_dispatch_at = synonym("next_dispatch_at")
    last_dispatch_error = synonym("last_dispatch_error_category")
    dispatch_payload = synonym("payload")
    dispatch_envelope = synonym("payload")

    __table_args__ = (
        Index(
            "uq_ai_jobs_session_attempt",
            "attempt_id",
            unique=True,
            postgresql_where=text("job_type = 'analyze_session'"),
            sqlite_where=text("job_type = 'analyze_session'"),
        ),
        Index(
            "ix_ai_jobs_pending_dispatch",
            "status",
            "next_dispatch_at",
            "created_at",
            "id",
        ),
    )


AIJobModel = AnalysisJobModel
