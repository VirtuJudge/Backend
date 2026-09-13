from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

if TYPE_CHECKING:
    from app.infrastructure.persistence.configurations.team_member_configuration import (
        TeamMemberModel,
    )


class SpeakerMappingModel(Base):
    __tablename__ = "speaker_mappings"

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "speaker_label",
            name="uq_speaker_mapping_attempt_label",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_attempts.id"),
        nullable=False,
    )

    speaker_label: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team_members.id"),
        nullable=True,
    )

    mapped_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    mapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    member: Mapped["TeamMemberModel | None"] = relationship(
        "TeamMemberModel",
        lazy="selectin",
    )
