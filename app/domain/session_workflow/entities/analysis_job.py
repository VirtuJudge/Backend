from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.job_status import AnalysisJobStatus

class AnalysisJob:
    id: UUID
    attempt_id: UUID

    status: AnalysisJobStatus
    correlation_id: UUID

    retry_count: int
    last_error: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None