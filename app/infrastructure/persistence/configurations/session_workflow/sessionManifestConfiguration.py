from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class SessionManifestModel(Base):
    __tablename__ = "session_manifests"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("practice_sessions.id"),
        nullable=False,
        unique=True,
    )

    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.id"),
        nullable=False,
    )

    document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_versions.id"),
        nullable=True,
    )

    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
