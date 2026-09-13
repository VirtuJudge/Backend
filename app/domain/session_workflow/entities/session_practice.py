from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import InvalidSessionStatusTransition

_ALLOWED_TRANSITIONS = {
    SessionStatus.DRAFT: {SessionStatus.READY, SessionStatus.CANCELLED},
    SessionStatus.READY: {SessionStatus.ANALYZING, SessionStatus.CANCELLED},
    SessionStatus.ANALYZING: {
        SessionStatus.FAILED,
        SessionStatus.QUESTIONS_READY,
        SessionStatus.CANCELLED,
    },
    SessionStatus.FAILED: {SessionStatus.ANALYZING, SessionStatus.CANCELLED},
    SessionStatus.QUESTIONS_READY: {
        SessionStatus.QUESTIONS_IN_PROGRESS,
        SessionStatus.CANCELLED,
    },
    SessionStatus.QUESTIONS_IN_PROGRESS: {
        SessionStatus.REPORT_GENERATING,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
    },
    SessionStatus.REPORT_GENERATING: {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
    },
    SessionStatus.COMPLETED: set(),
    SessionStatus.CANCELLED: set(),
}


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
    consent_policy_version: int | None = None
    consent_confirmed_by: UUID | None = None
    consent_confirmed_at: datetime | None = None
    cancelled_by: UUID | None = None
    cancellation_reason: str | None = None

    def transition_to(self, status: SessionStatus) -> None:
        if status == self.status:
            return
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidSessionStatusTransition(
                f"Cannot transition from {self.status} to {status}."
            )
        self.status = status

    def cancel(self, actor_id: UUID, reason: str | None, at: datetime) -> None:
        if self.status == SessionStatus.CANCELLED:
            return
        self.transition_to(SessionStatus.CANCELLED)
        self.cancelled_at = at
        self.cancelled_by = actor_id
        self.cancellation_reason = reason
        self.updated_at = at
