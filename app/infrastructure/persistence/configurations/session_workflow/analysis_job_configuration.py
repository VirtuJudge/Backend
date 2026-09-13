from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.infrastructure.database import Base


class AnalysisJobModel(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_attempts.id"),
        nullable=False,
        unique=True,
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
