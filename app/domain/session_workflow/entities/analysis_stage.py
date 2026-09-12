from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.stage_status import StageStatus
from app.domain.session_workflow.enums.stage_type import StageType


@dataclass(slots=True)
class AnalysisStage:
    id: UUID
    attempt_id: UUID
    stage: StageType
    status: StageStatus
    progress: int
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
