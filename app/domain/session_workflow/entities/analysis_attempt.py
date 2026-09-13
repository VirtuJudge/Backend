from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.exceptions import InvalidAttemptState

_ALLOWED_TRANSITIONS = {
    AnalysisAttemptStatus.QUEUED: {
        AnalysisAttemptStatus.RUNNING,
        AnalysisAttemptStatus.FAILED,
        AnalysisAttemptStatus.CANCELLED,
    },
    AnalysisAttemptStatus.RUNNING: {
        AnalysisAttemptStatus.COMPLETED,
        AnalysisAttemptStatus.FAILED,
        AnalysisAttemptStatus.CANCELLED,
    },
    AnalysisAttemptStatus.COMPLETED: set(),
    AnalysisAttemptStatus.FAILED: set(),
    AnalysisAttemptStatus.CANCELLED: set(),
}


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
    request_hash: str | None = None

    def transition_to(self, status: AnalysisAttemptStatus, at: datetime) -> None:
        if status == self.status:
            return
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidAttemptState(f"Cannot transition from {self.status} to {status}.")
        self.status = status
        if status == AnalysisAttemptStatus.RUNNING:
            self.started_at = at
        elif status == AnalysisAttemptStatus.COMPLETED:
            self.completed_at = at
        elif status == AnalysisAttemptStatus.FAILED:
            self.failed_at = at
        elif status == AnalysisAttemptStatus.CANCELLED:
            self.cancelled_at = at
