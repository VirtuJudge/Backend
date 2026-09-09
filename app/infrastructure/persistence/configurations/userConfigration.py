from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    display_name: Mapped[str | None] = mapped_column(
            String(255),
            nullable=False,
    )

    issuer: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )


    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        server_default="",
    )

    team_memberships: Mapped[list["TeamMemberModel"]] = relationship(back_populates="user")

    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_users_issuer_subject",
        ),
    )
