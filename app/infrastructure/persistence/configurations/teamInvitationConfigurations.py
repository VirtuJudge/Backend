import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column , relationship

from app.infrastructure.database import Base

from app.infrastructure.persistence.configurations.invitationResendIdompotancyConfiguration import InvitationResendIdempotencyModel
from app.infrastructure.persistence.configurations.teamConfigration import TeamModel


class InvitationStatus(enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DeliveryStatus(enum.Enum):
    Queued = "queued"
    ACCEPTED = "accepted_by_gmail"
    FAILED = "failed"


class TeamInvitationModel(Base):
    __tablename__ = "team_invitations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    team_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus),
        nullable=False,
    )

    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus),
        nullable=False,
    )

    delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    expires_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    team: Mapped["TeamModel"] = relationship(
        back_populates="invitations",
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )