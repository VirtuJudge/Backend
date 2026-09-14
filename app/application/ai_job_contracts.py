import contextlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

SHA256_HEX_PATTERN = r"^sha256:[0-9a-fA-F]{64}$"


class AIJobType(StrEnum):
    ANALYZE_SESSION = "analyze_session"
    ANALYZE_ANSWER = "analyze_answer"
    GENERATE_REPORT = "generate_report"
    ERASE_AI_DATA = "erase_ai_data"


class AIWorkerUpdateStatus(StrEnum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErasureScope(StrEnum):
    ASSET = "asset"
    PRACTICE_SESSION = "practice_session"
    PROJECT = "project"
    TEAM = "team"


class ArtifactRef(BaseModel):
    artifact_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    checksum: str = Field(pattern=SHA256_HEX_PATTERN)
    schema_version: int | None = None


class AssetInput(BaseModel):
    artifact_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    checksum: str = Field(pattern=SHA256_HEX_PATTERN)
    media_type: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)


class AudioAssetInput(AssetInput):
    duration_ms: int = Field(ge=0)


class RubricRef(BaseModel):
    rubric_id: str = Field(min_length=1)
    version: int = Field(ge=1)


class SpeakerMapping(BaseModel):
    speaker_label: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class Limitation(BaseModel):
    code: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    message: str = Field(min_length=1)
    affected_dimensions: list[str] = Field(default_factory=list)


class PrimaryQuestion(BaseModel):
    candidate_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1)
    rubric_dimension: str = Field(min_length=1)
    evidence_ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class FollowUpQuestion(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1)
    rubric_dimension: str = Field(min_length=1)
    evidence_ids: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)


class AnalyzeSessionPayload(BaseModel):
    presentation: AssetInput
    supporting_documents: list[AssetInput] = Field(default_factory=list)
    rubric: RubricRef
    requested_capabilities: list[str] = Field(default_factory=list)


class AnalyzeAnswerPayload(BaseModel):
    qa_round_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    answer_id: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)
    audio: AudioAssetInput
    remaining_follow_ups: int = Field(ge=0)


class GenerateReportPayload(BaseModel):
    report_id: str = Field(min_length=1)
    analysis_artifact: ArtifactRef
    qa_artifact: ArtifactRef
    speaker_mappings: list[SpeakerMapping] = Field(default_factory=list)


class EraseAIDataPayload(BaseModel):
    erasure_request_id: str = Field(min_length=1)
    scope: ErasureScope
    scope_id: str = Field(min_length=1)


AIJobPayload = (
    AnalyzeSessionPayload | AnalyzeAnswerPayload | GenerateReportPayload | EraseAIDataPayload
)

JOB_PAYLOAD_MODELS: dict[AIJobType, type[BaseModel]] = {
    AIJobType.ANALYZE_SESSION: AnalyzeSessionPayload,
    AIJobType.ANALYZE_ANSWER: AnalyzeAnswerPayload,
    AIJobType.GENERATE_REPORT: GenerateReportPayload,
    AIJobType.ERASE_AI_DATA: EraseAIDataPayload,
}


def get_job_payload_model(job_type: AIJobType | str) -> type[BaseModel]:
    return JOB_PAYLOAD_MODELS[AIJobType(job_type)]


def parse_job_payload(job_type: AIJobType | str, payload: Any) -> AIJobPayload:
    model_cls = get_job_payload_model(job_type)
    return model_cls.model_validate(payload)  # type: ignore[return-value]


JobPayloadT = TypeVar("JobPayloadT", bound=BaseModel)


class BaseAIJobQueueMessage(BaseModel, Generic[JobPayloadT]):
    schema_version: Literal[1] = 1
    job_id: str = Field(min_length=1)
    job_type: AIJobType
    practice_session_id: str = Field(min_length=1)
    analysis_attempt: int = Field(ge=1)
    created_at: datetime
    trace_id: str = Field(min_length=1)
    payload: JobPayloadT


class AnalyzeSessionQueueMessage(BaseAIJobQueueMessage[AnalyzeSessionPayload]):
    job_type: Literal[AIJobType.ANALYZE_SESSION] = AIJobType.ANALYZE_SESSION


class AnalyzeAnswerQueueMessage(BaseAIJobQueueMessage[AnalyzeAnswerPayload]):
    job_type: Literal[AIJobType.ANALYZE_ANSWER] = AIJobType.ANALYZE_ANSWER


class GenerateReportQueueMessage(BaseAIJobQueueMessage[GenerateReportPayload]):
    job_type: Literal[AIJobType.GENERATE_REPORT] = AIJobType.GENERATE_REPORT


class EraseAIDataQueueMessage(BaseAIJobQueueMessage[EraseAIDataPayload]):
    job_type: Literal[AIJobType.ERASE_AI_DATA] = AIJobType.ERASE_AI_DATA


TypedAIJobQueueMessage = Annotated[
    AnalyzeSessionQueueMessage
    | AnalyzeAnswerQueueMessage
    | GenerateReportQueueMessage
    | EraseAIDataQueueMessage,
    Field(discriminator="job_type"),
]


class AIJobQueueMessage(BaseModel):
    schema_version: Literal[1] = 1
    job_id: str = Field(min_length=1)
    job_type: AIJobType
    practice_session_id: str = Field(min_length=1)
    analysis_attempt: int = Field(ge=1)
    created_at: datetime
    trace_id: str = Field(min_length=1)
    payload: (
        AnalyzeSessionPayload | AnalyzeAnswerPayload | GenerateReportPayload | EraseAIDataPayload
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_payload_matches_job_type(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw_job_type = data.get("job_type")
            if raw_job_type in {e.value for e in AIJobType}:
                job_type_enum = AIJobType(raw_job_type)
                raw_payload = data.get("payload")
                payload_model = JOB_PAYLOAD_MODELS[job_type_enum]
                validated_payload = payload_model.model_validate(raw_payload)
                data = dict(data)
                data["payload"] = validated_payload
        return data

    def to_typed_message(
        self,
    ) -> (
        AnalyzeSessionQueueMessage
        | AnalyzeAnswerQueueMessage
        | GenerateReportQueueMessage
        | EraseAIDataQueueMessage
    ):
        adapter: TypeAdapter[TypedAIJobQueueMessage] = TypeAdapter(TypedAIJobQueueMessage)
        return adapter.validate_python(self.model_dump(mode="python"))


def parse_queue_message(
    data: Any,
) -> (
    AnalyzeSessionQueueMessage
    | AnalyzeAnswerQueueMessage
    | GenerateReportQueueMessage
    | EraseAIDataQueueMessage
):
    adapter: TypeAdapter[TypedAIJobQueueMessage] = TypeAdapter(TypedAIJobQueueMessage)
    return adapter.validate_python(data)


class StartedPayload(BaseModel):
    pipeline_version: str = Field(min_length=1)


class ProgressPayload(BaseModel):
    stage: str = Field(min_length=1)
    progress: float = Field(ge=0.0, le=1.0)
    message: str = Field(min_length=1)


class FailedPayload(BaseModel):
    stage: str = Field(min_length=1)
    code: str = Field(min_length=1)
    retryable: bool
    attempts: int = Field(ge=0)
    message: str = Field(min_length=1)


class CancelledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionAnalysisCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_artifact: ArtifactRef
    primary_questions: list[PrimaryQuestion] = Field(min_length=3, max_length=3)
    speaker_labels: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)


class AnswerAnalysisCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_id: str = Field(min_length=1)
    transcript_artifact_id: str = Field(min_length=1)
    assessment_artifact_id: str = Field(min_length=1)
    follow_up: FollowUpQuestion | None = None


class ReportCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_artifact: ArtifactRef
    report_artifact: ArtifactRef
    member_feedback_user_ids: list[Annotated[str, Field(min_length=1)]] = Field(
        default_factory=list
    )
    limitations: list[Limitation] = Field(default_factory=list)


class ErasureCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    erasure_request_id: str = Field(min_length=1)
    deleted_records: int = Field(ge=0)
    deleted_objects: int = Field(ge=0)


CompletedPayload = (
    SessionAnalysisCompletedPayload
    | AnswerAnalysisCompletedPayload
    | ReportCompletedPayload
    | ErasureCompletedPayload
)

COMPLETED_PAYLOAD_MODELS: dict[AIJobType, type[BaseModel]] = {
    AIJobType.ANALYZE_SESSION: SessionAnalysisCompletedPayload,
    AIJobType.ANALYZE_ANSWER: AnswerAnalysisCompletedPayload,
    AIJobType.GENERATE_REPORT: ReportCompletedPayload,
    AIJobType.ERASE_AI_DATA: ErasureCompletedPayload,
}


def get_completed_payload_model(job_type: AIJobType | str) -> type[BaseModel]:
    return COMPLETED_PAYLOAD_MODELS[AIJobType(job_type)]


def parse_completed_payload(
    job_type: AIJobType | str,
    payload: Any,
) -> CompletedPayload:
    model_cls = get_completed_payload_model(job_type)
    return model_cls.model_validate(payload)  # type: ignore[return-value]


UpdatePayloadT = TypeVar("UpdatePayloadT")


class BaseAIWorkerUpdate(BaseModel, Generic[UpdatePayloadT]):
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    status: AIWorkerUpdateStatus
    occurred_at: datetime
    trace_id: str = Field(min_length=1)
    payload: UpdatePayloadT


class StartedWorkerUpdate(BaseAIWorkerUpdate[StartedPayload]):
    status: Literal[AIWorkerUpdateStatus.STARTED] = AIWorkerUpdateStatus.STARTED


class ProgressWorkerUpdate(BaseAIWorkerUpdate[ProgressPayload]):
    status: Literal[AIWorkerUpdateStatus.PROGRESS] = AIWorkerUpdateStatus.PROGRESS


class FailedWorkerUpdate(BaseAIWorkerUpdate[FailedPayload]):
    status: Literal[AIWorkerUpdateStatus.FAILED] = AIWorkerUpdateStatus.FAILED


class CancelledWorkerUpdate(BaseAIWorkerUpdate[CancelledPayload]):
    status: Literal[AIWorkerUpdateStatus.CANCELLED] = AIWorkerUpdateStatus.CANCELLED


class SessionAnalysisCompletedWorkerUpdate(BaseAIWorkerUpdate[SessionAnalysisCompletedPayload]):
    status: Literal[AIWorkerUpdateStatus.COMPLETED] = AIWorkerUpdateStatus.COMPLETED


class AnswerAnalysisCompletedWorkerUpdate(BaseAIWorkerUpdate[AnswerAnalysisCompletedPayload]):
    status: Literal[AIWorkerUpdateStatus.COMPLETED] = AIWorkerUpdateStatus.COMPLETED


class ReportCompletedWorkerUpdate(BaseAIWorkerUpdate[ReportCompletedPayload]):
    status: Literal[AIWorkerUpdateStatus.COMPLETED] = AIWorkerUpdateStatus.COMPLETED


class ErasureCompletedWorkerUpdate(BaseAIWorkerUpdate[ErasureCompletedPayload]):
    status: Literal[AIWorkerUpdateStatus.COMPLETED] = AIWorkerUpdateStatus.COMPLETED


CompletedWorkerUpdate = (
    SessionAnalysisCompletedWorkerUpdate
    | AnswerAnalysisCompletedWorkerUpdate
    | ReportCompletedWorkerUpdate
    | ErasureCompletedWorkerUpdate
)

COMPLETED_UPDATE_MODELS: dict[AIJobType, type[CompletedWorkerUpdate]] = {
    AIJobType.ANALYZE_SESSION: SessionAnalysisCompletedWorkerUpdate,
    AIJobType.ANALYZE_ANSWER: AnswerAnalysisCompletedWorkerUpdate,
    AIJobType.GENERATE_REPORT: ReportCompletedWorkerUpdate,
    AIJobType.ERASE_AI_DATA: ErasureCompletedWorkerUpdate,
}


def get_completed_update_model(job_type: AIJobType | str) -> type[CompletedWorkerUpdate]:
    return COMPLETED_UPDATE_MODELS[AIJobType(job_type)]


class AIWorkerUpdate(BaseModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    status: AIWorkerUpdateStatus
    occurred_at: datetime
    trace_id: str = Field(min_length=1)
    payload: Any = Field(...)

    @model_validator(mode="after")
    def _validate_payload_for_status(self) -> Self:
        if self.status == AIWorkerUpdateStatus.STARTED and not isinstance(
            self.payload, StartedPayload
        ):
            self.payload = StartedPayload.model_validate(self.payload)
        elif self.status == AIWorkerUpdateStatus.PROGRESS and not isinstance(
            self.payload, ProgressPayload
        ):
            self.payload = ProgressPayload.model_validate(self.payload)
        elif self.status == AIWorkerUpdateStatus.FAILED and not isinstance(
            self.payload, FailedPayload
        ):
            self.payload = FailedPayload.model_validate(self.payload)
        elif self.status == AIWorkerUpdateStatus.CANCELLED and not isinstance(
            self.payload, CancelledPayload
        ):
            self.payload = CancelledPayload.model_validate(self.payload)
        return self

    def parse_completed_payload(
        self,
        job_type: AIJobType | str,
    ) -> CompletedPayload:
        if self.status != AIWorkerUpdateStatus.COMPLETED:
            raise ValueError(f"Cannot parse completed payload when status is '{self.status}'")
        return parse_completed_payload(job_type, self.payload)

    def to_typed_update(
        self,
        job_type: AIJobType | str | None = None,
    ) -> (
        StartedWorkerUpdate
        | ProgressWorkerUpdate
        | FailedWorkerUpdate
        | CancelledWorkerUpdate
        | CompletedWorkerUpdate
    ):
        if self.status == AIWorkerUpdateStatus.STARTED:
            return StartedWorkerUpdate(
                schema_version=self.schema_version,
                sequence=self.sequence,
                status=self.status,
                occurred_at=self.occurred_at,
                trace_id=self.trace_id,
                payload=StartedPayload.model_validate(self.payload),
            )
        if self.status == AIWorkerUpdateStatus.PROGRESS:
            return ProgressWorkerUpdate(
                schema_version=self.schema_version,
                sequence=self.sequence,
                status=self.status,
                occurred_at=self.occurred_at,
                trace_id=self.trace_id,
                payload=ProgressPayload.model_validate(self.payload),
            )
        if self.status == AIWorkerUpdateStatus.FAILED:
            return FailedWorkerUpdate(
                schema_version=self.schema_version,
                sequence=self.sequence,
                status=self.status,
                occurred_at=self.occurred_at,
                trace_id=self.trace_id,
                payload=FailedPayload.model_validate(self.payload),
            )
        if self.status == AIWorkerUpdateStatus.CANCELLED:
            return CancelledWorkerUpdate(
                schema_version=self.schema_version,
                sequence=self.sequence,
                status=self.status,
                occurred_at=self.occurred_at,
                trace_id=self.trace_id,
                payload=CancelledPayload.model_validate(self.payload),
            )
        if self.status == AIWorkerUpdateStatus.COMPLETED:
            if job_type is None:
                raise ValueError(
                    "job_type is required to distinguish completed worker update without guessing"
                )
            job_type_enum = AIJobType(job_type)
            if job_type_enum == AIJobType.ANALYZE_SESSION:
                return SessionAnalysisCompletedWorkerUpdate(
                    schema_version=self.schema_version,
                    sequence=self.sequence,
                    status=self.status,
                    occurred_at=self.occurred_at,
                    trace_id=self.trace_id,
                    payload=SessionAnalysisCompletedPayload.model_validate(self.payload),
                )
            if job_type_enum == AIJobType.ANALYZE_ANSWER:
                return AnswerAnalysisCompletedWorkerUpdate(
                    schema_version=self.schema_version,
                    sequence=self.sequence,
                    status=self.status,
                    occurred_at=self.occurred_at,
                    trace_id=self.trace_id,
                    payload=AnswerAnalysisCompletedPayload.model_validate(self.payload),
                )
            if job_type_enum == AIJobType.GENERATE_REPORT:
                return ReportCompletedWorkerUpdate(
                    schema_version=self.schema_version,
                    sequence=self.sequence,
                    status=self.status,
                    occurred_at=self.occurred_at,
                    trace_id=self.trace_id,
                    payload=ReportCompletedPayload.model_validate(self.payload),
                )
            if job_type_enum == AIJobType.ERASE_AI_DATA:
                return ErasureCompletedWorkerUpdate(
                    schema_version=self.schema_version,
                    sequence=self.sequence,
                    status=self.status,
                    occurred_at=self.occurred_at,
                    trace_id=self.trace_id,
                    payload=ErasureCompletedPayload.model_validate(self.payload),
                )
        raise ValueError(f"Unknown status: {self.status}")


def parse_worker_update(
    data: Any,
    job_type: AIJobType | str | None = None,
) -> (
    StartedWorkerUpdate
    | ProgressWorkerUpdate
    | FailedWorkerUpdate
    | CancelledWorkerUpdate
    | CompletedWorkerUpdate
):
    update = AIWorkerUpdate.model_validate(data)
    if update.status == AIWorkerUpdateStatus.COMPLETED:
        if job_type is not None:
            return update.to_typed_update(job_type)
        matches: list[CompletedWorkerUpdate] = []
        for jt in AIJobType:
            with contextlib.suppress(Exception):
                matches.append(update.to_typed_update(jt))  # type: ignore[arg-type]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("Completed payload does not match any known completed payload model")
        raise ValueError(
            "Ambiguous completed payload matches multiple models; job_type is required"
        )
    return update.to_typed_update()
