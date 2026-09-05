from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.persistence.configurations.projectConfigration import ProjectModel
    from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel


class TeamModel(Base):
    __tablename__ = "teams"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    members: Mapped[list["TeamMemberModel"]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
    )

    projects: Mapped[list["ProjectModel"]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
    )