from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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
