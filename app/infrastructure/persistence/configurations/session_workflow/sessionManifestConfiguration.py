from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint,Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

class SessionManifestModel(Base):
    __tablename__ = "session_manifests"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("practice_sessions.id"),
        nullable=False,
        unique=True,
    )

    presentation_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("presentation_versions.id"),
        nullable=False,
    )

    document_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id"),
        nullable=True,
    )

    rubric_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rubric_versions.id"),
        nullable=False,
    )

    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )