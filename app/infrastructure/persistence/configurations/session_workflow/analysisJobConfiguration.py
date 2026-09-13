from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text
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

    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(AnalysisJobStatus),
        nullable=False,
    )

    correlation_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
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

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
