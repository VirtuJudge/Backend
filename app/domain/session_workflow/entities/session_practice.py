from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.session_status import SessionStatus
class PracticeSession:
    id: UUID
    project_id: UUID
    created_by: UUID

    status: SessionStatus
    version: int

    created_at: datetime
    updated_at: datetime

    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None