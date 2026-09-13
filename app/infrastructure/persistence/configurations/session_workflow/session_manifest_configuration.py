from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

if TYPE_CHECKING:
    from .session_manifest_document_configuration import (
        SessionManifestDocumentModel,
    )


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

    rubric_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="startup_pitch",
    )

    rubric_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    supporting_documents: Mapped[list["SessionManifestDocumentModel"]] = relationship(
        "SessionManifestDocumentModel",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
