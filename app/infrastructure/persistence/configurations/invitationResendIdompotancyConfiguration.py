from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base



class InvitationResendIdempotencyModel(Base):
    __tablename__ = "invitation_resend_idempotency"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("team_invitations.id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


    __table_args__ = (
        UniqueConstraint(
            "key",
            name="uq_invitation_resend_idempotency_key",
        ),
    )