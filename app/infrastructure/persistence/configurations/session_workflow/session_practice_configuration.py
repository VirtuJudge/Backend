from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.database import Base


class PracticeSessionModel(Base):
    __tablename__ = "practice_sessions"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )
    name: Mapped[str | None] = mapped_column(
        String(length=200),
        nullable=True,
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="sessionstatus",
        ),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
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

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    consent_granted: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
    )
    consent_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    consent_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
