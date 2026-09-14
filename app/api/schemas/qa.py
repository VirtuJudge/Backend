from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas.asset import UploadIntentResponse
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)


class QuestionResponse(BaseModel):
    id: UUID
    practice_session_id: UUID
    kind: QuestionKind
    position: int
    text: str
    reason: str
    rubric_dimension: str
    evidence_ids: list[str]
    parent_answer_id: UUID | None
    state: QuestionState


class AnswerResponse(BaseModel):
    id: UUID
    question_id: UUID
    answered_by: UUID
    status: AnswerStatus
    audio_asset_version_id: UUID | None
    transcript_artifact_id: str | None
    duration_ms: int | None
    submitted_at: datetime | None


class QARoundResponse(BaseModel):
    id: UUID
    practice_session_id: UUID
    state: QARoundState
    questions: list[QuestionResponse]
    answers: list[AnswerResponse]
    current_question_id: UUID | None
    follow_up_count: int
    version: int


class AnswerUploadIntentRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    declared_media_type: str = Field(min_length=1, max_length=100)
    declared_size_bytes: int = Field(gt=0)


class AnswerUploadIntentResponse(BaseModel):
    answer: AnswerResponse
    upload_intent: UploadIntentResponse


class SubmitAnswerRequest(BaseModel):
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class SkipQuestionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
