from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus


class AnalysisAttempt:
    id: UUID
    session_id: UUID
    manifest_id: UUID

    attempt_number: int
    status: AnalysisAttemptStatus

    failure_code: str | None
    failure_message: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None