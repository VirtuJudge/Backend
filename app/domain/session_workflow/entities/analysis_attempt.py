from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus


@dataclass(slots=True)
class AnalysisAttempt:
    id: UUID
    session_id: UUID
    manifest_id: UUID
    idempotency_key: str | None

    attempt_number: int
    status: AnalysisAttemptStatus

    failure_code: str | None
    failure_message: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    version: int
