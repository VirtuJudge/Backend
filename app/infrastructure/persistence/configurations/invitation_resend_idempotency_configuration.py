from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

if TYPE_CHECKING:
    from app.infrastructure.persistence.configurations.team_invitation_configuration import (
        TeamInvitationModel,
    )


class InvitationResendIdempotencyModel(Base):
    __tablename__ = "invitation_resend_idempotency"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))

    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("team_invitations.id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    invitation: Mapped["TeamInvitationModel"] = relationship(
        back_populates="resend_idempotency_keys"
    )

    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "team_id",
            "invitation_id",
            "operation",
            "key",
            name="uq_invitation_resend_idempotency_scope",
        ),
    )
