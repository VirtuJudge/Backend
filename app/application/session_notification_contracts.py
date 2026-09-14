from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.application.ai_job_contracts import ErasureScope
from app.domain.session_workflow.enums.stage_type import StageType


def _coerce_str(v: Any) -> Any:
    if isinstance(v, (UUID, StrEnum)):
        return str(v)
    return v


StrId = Annotated[str, BeforeValidator(_coerce_str)]


class NotificationEventName(StrEnum):
    PRACTICE_SESSION_UPDATED = "practice_session.updated.v1"
    PRACTICE_SESSION_ANALYSIS_PROGRESSED = "practice_session.analysis_progressed.v1"
    QA_QUESTION_AVAILABLE = "qa.question_available.v1"
    QA_ANSWER_UPDATED = "qa.answer_updated.v1"
    REPORT_READY = "report.ready.v1"
    ERASURE_UPDATED = "erasure.updated.v1"
    PRACTICE_SESSION_RESYNC_REQUIRED = "practice_session.resync_required.v1"


class PublicStage(StrEnum):
    INGESTION = "ingestion"
    SPEECH = "speech"
    DIARIZATION = "diarization"
    VISION = "vision"
    AUDIO_FEATURES = "audio_features"
    DOCUMENTS = "documents"
    AGGREGATION = "aggregation"
    GROUNDING = "grounding"
    QUESTIONS = "questions"
    ANSWERS = "answers"
    REPORT = "report"
    PROCESSING = "processing"


class QuestionKind(StrEnum):
    PRIMARY = "primary"
    FOLLOW_UP = "follow_up"


class QuestionState(StrEnum):
    ACTIVE = "active"


class AnswerStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"


class ReportStatus(StrEnum):
    READY = "ready"


class ErasureStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ResyncReason(StrEnum):
    CURSOR_MISSING = "cursor_missing"
    CURSOR_TRIMMED = "cursor_trimmed"
    CURSOR_EXPIRED = "cursor_expired"
    CURSOR_FUTURE = "cursor_future"


INTERNAL_TO_PUBLIC_STAGE_MAP: dict[str, PublicStage] = {
    "transcription": PublicStage.PROCESSING,
    "document_analysis": PublicStage.PROCESSING,
    "scoring": PublicStage.PROCESSING,
    "diarization": PublicStage.DIARIZATION,
    "report": PublicStage.REPORT,
}


def map_to_public_stage(stage: str | StageType | PublicStage | None) -> PublicStage:
    if stage is None:
        return PublicStage.PROCESSING
    if isinstance(stage, PublicStage):
        return stage
    raw_val = stage.value if isinstance(stage, StrEnum) else str(stage).strip().lower()
    if not raw_val:
        return PublicStage.PROCESSING
    if raw_val in INTERNAL_TO_PUBLIC_STAGE_MAP:
        return INTERNAL_TO_PUBLIC_STAGE_MAP[raw_val]
    try:
        return PublicStage(raw_val)
    except ValueError:
        return PublicStage.PROCESSING


class BasePersistedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    practice_session_id: StrId = Field(min_length=1)
    occurred_at: datetime
    trace_id: str = Field(min_length=1)

    @property
    def event_name(self) -> NotificationEventName:
        raise NotImplementedError

    def to_sse_frame(self) -> str:
        data = self.model_dump_json()
        return f"id: {self.sequence}\nevent: {self.event_name.value}\ndata: {data}\n\n"


class BaseUnpersistedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    practice_session_id: StrId = Field(min_length=1)
    occurred_at: datetime
    trace_id: str = Field(min_length=1)

    @property
    def event_name(self) -> NotificationEventName:
        raise NotImplementedError

    def to_sse_frame(self) -> str:
        data = self.model_dump_json()
        return f"id: {self.sequence}\nevent: {self.event_name.value}\ndata: {data}\n\n"


class PracticeSessionUpdatedNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.PRACTICE_SESSION_UPDATED] = Field(
        default=NotificationEventName.PRACTICE_SESSION_UPDATED,
        alias="event_name",
        exclude=True,
    )
    version: int = Field(ge=1)
    state: StrId = Field(min_length=1)
    current_attempt: int | None = Field(default=None, ge=1)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.PRACTICE_SESSION_UPDATED


class PracticeSessionAnalysisProgressedNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.PRACTICE_SESSION_ANALYSIS_PROGRESSED] = Field(
        default=NotificationEventName.PRACTICE_SESSION_ANALYSIS_PROGRESSED,
        alias="event_name",
        exclude=True,
    )
    analysis_attempt_id: StrId = Field(min_length=1)
    analysis_attempt_number: int = Field(ge=1)
    stage: PublicStage
    status: StrId = Field(min_length=1)
    progress: float = Field(ge=0.0, le=1.0)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.PRACTICE_SESSION_ANALYSIS_PROGRESSED

    @classmethod
    def from_worker_progress(
        cls,
        *,
        sequence: int,
        practice_session_id: str,
        analysis_attempt_id: str,
        analysis_attempt_number: int,
        worker_stage: str | StageType | PublicStage | None,
        status: str | StrEnum,
        progress: float,
        occurred_at: datetime,
        trace_id: str,
        worker_message: str | None = None,
    ) -> "PracticeSessionAnalysisProgressedNotification":
        return cls(
            sequence=sequence,
            practice_session_id=practice_session_id,
            analysis_attempt_id=analysis_attempt_id,
            analysis_attempt_number=analysis_attempt_number,
            stage=map_to_public_stage(worker_stage),
            status=status.value if isinstance(status, StrEnum) else str(status),
            progress=progress,
            occurred_at=occurred_at,
            trace_id=trace_id,
        )


class QAQuestionAvailableNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.QA_QUESTION_AVAILABLE] = Field(
        default=NotificationEventName.QA_QUESTION_AVAILABLE,
        alias="event_name",
        exclude=True,
    )
    qa_round_id: StrId = Field(min_length=1)
    question_id: StrId = Field(min_length=1)
    position: int = Field(ge=1, le=5)
    kind: QuestionKind
    state: QuestionState = QuestionState.ACTIVE
    version: int = Field(ge=1)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.QA_QUESTION_AVAILABLE


class QAAnswerUpdatedNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.QA_ANSWER_UPDATED] = Field(
        default=NotificationEventName.QA_ANSWER_UPDATED,
        alias="event_name",
        exclude=True,
    )
    qa_round_id: StrId = Field(min_length=1)
    question_id: StrId = Field(min_length=1)
    answer_id: StrId = Field(min_length=1)
    status: AnswerStatus
    version: int = Field(ge=1)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.QA_ANSWER_UPDATED


class ReportReadyNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.REPORT_READY] = Field(
        default=NotificationEventName.REPORT_READY,
        alias="event_name",
        exclude=True,
    )
    report_id: StrId = Field(min_length=1)
    evaluation_id: StrId = Field(min_length=1)
    status: ReportStatus = ReportStatus.READY
    version: int = Field(ge=1)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.REPORT_READY


class ErasureUpdatedNotification(BasePersistedNotification):
    event_name_field: Literal[NotificationEventName.ERASURE_UPDATED] = Field(
        default=NotificationEventName.ERASURE_UPDATED,
        alias="event_name",
        exclude=True,
    )
    erasure_request_id: StrId = Field(min_length=1)
    scope: ErasureScope
    status: ErasureStatus

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.ERASURE_UPDATED


class PracticeSessionResyncRequiredNotification(BaseUnpersistedNotification):
    event_name_field: Literal[NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED] = Field(
        default=NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED,
        alias="event_name",
        exclude=True,
    )
    reason: ResyncReason
    current_sequence: int = Field(ge=0)
    requested_sequence: int | None = Field(default=None, ge=0)

    @property
    def event_name(self) -> NotificationEventName:
        return NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED

    @model_validator(mode="after")
    def validate_sequence_matches_current(self) -> Self:
        if self.sequence != self.current_sequence:
            raise ValueError(
                f"Resync control frame sequence ({self.sequence}) "
                f"must equal current_sequence ({self.current_sequence})"
            )
        return self


SessionNotification = (
    PracticeSessionUpdatedNotification
    | PracticeSessionAnalysisProgressedNotification
    | QAQuestionAvailableNotification
    | QAAnswerUpdatedNotification
    | ReportReadyNotification
    | ErasureUpdatedNotification
    | PracticeSessionResyncRequiredNotification
)

SessionNotificationModel = type[
    PracticeSessionUpdatedNotification
    | PracticeSessionAnalysisProgressedNotification
    | QAQuestionAvailableNotification
    | QAAnswerUpdatedNotification
    | ReportReadyNotification
    | ErasureUpdatedNotification
    | PracticeSessionResyncRequiredNotification
]

NOTIFICATION_MODELS_BY_EVENT: dict[NotificationEventName, SessionNotificationModel] = {
    NotificationEventName.PRACTICE_SESSION_UPDATED: (PracticeSessionUpdatedNotification),
    NotificationEventName.PRACTICE_SESSION_ANALYSIS_PROGRESSED: (
        PracticeSessionAnalysisProgressedNotification
    ),
    NotificationEventName.QA_QUESTION_AVAILABLE: (QAQuestionAvailableNotification),
    NotificationEventName.QA_ANSWER_UPDATED: (QAAnswerUpdatedNotification),
    NotificationEventName.REPORT_READY: (ReportReadyNotification),
    NotificationEventName.ERASURE_UPDATED: (ErasureUpdatedNotification),
    NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED: (
        PracticeSessionResyncRequiredNotification
    ),
}


def parse_notification(data: dict[str, Any]) -> SessionNotification:
    event_raw = data.get("event_name") or data.get("event")
    if not event_raw or not isinstance(event_raw, str):
        raise ValueError("Invalid or unknown event name: missing event_name or event key")
    try:
        event_name = NotificationEventName(event_raw)
    except ValueError as e:
        raise ValueError(f"Invalid or unknown event name: {event_raw}") from e

    model_cls = NOTIFICATION_MODELS_BY_EVENT[event_name]
    return model_cls.model_validate(data)
