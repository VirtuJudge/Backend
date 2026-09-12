from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus


class PracticeSessionResponse(BaseModel):
    id: UUID
    project_id: UUID
    created_by: UUID
    name: str | None
    status: SessionStatus
    version: int
    created_at: datetime
    updated_at: datetime


class CreatePracticeSessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    presentation_asset_version_id: UUID

    document_version_id: UUID


class PracticeSessionListResponse(BaseModel):
    items: list[PracticeSessionResponse]
    next_cursor: str | None


class AnalysisAttemptResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: AnalysisAttemptStatus
    created_at: datetime
    updated_at: datetime
    version: int
    idempotency_key: str | None


class ConsentRequest(BaseModel):
    accepted: bool


class CreateAnalysisAttemptRequest(BaseModel):
    consent: ConsentRequest


class AnalysisAttemptListResponse(BaseModel):
    items: list[AnalysisAttemptResponse]
    next_cursor: str | None


class CancelPracticeSessionRequest(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=500,
    )
