from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint,Integer,Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.domain.session_workflow.enums.session_status import SessionStatus

class PracticeSessionModel(Base):
    __tablename__ = "practice_sessions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus),
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