from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint,Integer,Enum,Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus

class AnalysisAttemptModel(Base):
    __tablename__ = "analysis_attempts"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("practice_sessions.id"),
        nullable=False,
        index=True,
    )

    manifest_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_manifests.id"),
        nullable=False,
    )

    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[AnalysisAttemptStatus] = mapped_column(
        Enum(AnalysisAttemptStatus),
        nullable=False,
    )

    failure_code: Mapped[str | None] = mapped_column(
        String(100),
    )

    failure_message: Mapped[str | None] = mapped_column(
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

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "attempt_number",
            name="uq_session_attempt_number",
        ),
    )