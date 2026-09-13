from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class SessionManifest:
    id: UUID
    session_id: UUID
    presentation_version_id: UUID
    supporting_document_version_ids: list[UUID] = field(default_factory=list)
    rubric_id: str = "startup_pitch"
    rubric_version: int = 1
    snapshot: dict[str, Any] | None = None
    frozen_at: datetime | None = None
