from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class SessionManifestDocumentModel(Base):
    __tablename__ = "session_manifest_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    manifest_id: Mapped[UUID] = mapped_column(
        ForeignKey("session_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "document_version_id",
            name="uq_manifest_document_version",
        ),
    )
