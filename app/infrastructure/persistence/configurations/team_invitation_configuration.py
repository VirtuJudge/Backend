from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.team_invitation import DeliveryStatus, InvitationStatus
from app.infrastructure.database import Base

if TYPE_CHECKING:
    from .invitation_resend_idempotency_configuration import (
        InvitationResendIdempotencyModel,
    )
    from .team_configuration import TeamModel
import sqlalchemy as sa


class TeamInvitationModel(Base):
    __tablename__ = "team_invitations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    team_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id"),
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
        sa.Enum(
            InvitationStatus,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="invitationstatus",
        ),
        nullable=False,
    )

    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        sa.Enum(
            DeliveryStatus,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="deliverystatus",
        ),
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

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    resend_idempotency_keys: Mapped[list["InvitationResendIdempotencyModel"]] = relationship(
        back_populates="invitation",
        cascade="all, delete-orphan",
    )
