from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class SessionManifest:
    id: UUID
    session_id: UUID

    presentation_version_id: UUID
    document_version_id: UUID | None

    frozen_at: datetime | None
