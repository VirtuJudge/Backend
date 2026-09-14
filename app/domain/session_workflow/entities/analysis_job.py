from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus


@dataclass(slots=True)
class AnalysisJob:
    id: UUID
    practice_session_id: UUID
    attempt_id: UUID
    analysis_attempt: int
    job_type: str
    status: AnalysisJobStatus
    correlation_id: UUID
    last_update_sequence: int
    payload_version: int
    attempts: int
    cancel_requested: bool
    retry_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    payload: dict[str, Any] | None = None
    queued_at: datetime | None = None
    next_dispatch_at: datetime | None = None
    dispatch_retry_count: int = 0
    last_dispatch_error_category: str | None = None
    completed_result: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class AIJobAncestryContext:
    job: AnalysisJob
    attempt: AnalysisAttempt
    session: PracticeSession
    project_id: UUID
    team_id: UUID
    manifest_id: UUID | None = None
    manifest: SessionManifest | None = None

    @property
    def job_id(self) -> UUID:
        return self.job.id

    @property
    def attempt_id(self) -> UUID:
        return self.attempt.id

    @property
    def session_id(self) -> UUID:
        return self.session.id

    @property
    def attempt_number(self) -> int:
        return self.attempt.attempt_number


AnalysisJobAncestryContext = AIJobAncestryContext
