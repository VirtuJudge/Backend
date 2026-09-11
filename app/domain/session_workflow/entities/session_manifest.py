from datetime import datetime
from uuid import UUID

class SessionManifest:
    id: UUID
    session_id: UUID

    presentation_version_id: UUID
    document_version_id: UUID | None
    rubric_version_id: UUID

    frozen_at: datetime | None