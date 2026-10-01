from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ErasureRequestModel(Base):
    __tablename__ = "erasure_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    team_id: Mapped[UUID] = mapped_column(index=True)
    project_id: Mapped[UUID] = mapped_column()
    scope: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[UUID] = mapped_column()
    requested_by: Mapped[UUID | None] = mapped_column(nullable=True)
    origin: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(nullable=True)
    inventory: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("scope", "scope_id", name="uq_erasure_target"),)


class ErasureStepModel(Base):
    __tablename__ = "erasure_steps"

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("erasure_requests.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    deleted_records: Mapped[int] = mapped_column(Integer, default=0)
    deleted_objects: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(50))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ErasureItemModel(Base):
    __tablename__ = "erasure_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("erasure_requests.id", ondelete="CASCADE"),
        index=True,
    )
    store: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(30), default="pending")

    __table_args__ = (Index("uq_erasure_item", "request_id", "storage_key", unique=True),)
