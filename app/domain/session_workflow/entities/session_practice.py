from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.session_status import SessionStatus


@dataclass(slots=True)
class PracticeSession:
    id: UUID
    project_id: UUID
    created_by: UUID
    name: str | None

    status: SessionStatus
    version: int

    created_at: datetime
    updated_at: datetime
    consent_granted: bool

    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
