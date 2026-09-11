from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint,Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

class SessionConsentModel(Base):
    __tablename__ = "session_consents"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("practice_sessions.id"),
        nullable=False,
    )

    participant_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    policy_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    actor_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "participant_id",
            name="uq_session_consent_participant",
        ),
    )