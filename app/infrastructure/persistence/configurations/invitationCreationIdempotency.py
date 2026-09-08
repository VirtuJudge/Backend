from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.persistence.configurations.teamInvitationConfigurations import TeamInvitationModel
from app.infrastructure.database import Base


class InvitationIdempotencyModel(Base):
    __tablename__ = "invitation_idempotency_keys"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    team_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    invitation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("team_invitations.id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    invitation: Mapped["TeamInvitationModel"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "key",
            name="uq_invitation_idempotency_team_key",
        ),
    )