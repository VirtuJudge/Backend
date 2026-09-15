from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.session_workflow.enums.report import (
    FindingKind,
    ReportExportFormat,
    ReportExportStatus,
    ScoreLabel,
    ScoreStatus,
)


class SafeFailure(BaseModel):
    code: str
    stage: str | None = None
    retryable: bool = False
    message: str
    trace_id: str


class ScoreComponentResponse(BaseModel):
    dimension: str
    status: ScoreStatus
    configured_weight: float
    normalized_score: float | None = None
    display_score: int | None = None
    label: ScoreLabel | None = None
    effective_weight: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str | None = None
    limitation_code: str | None = None


class FindingResponse(BaseModel):
    id: str
    kind: FindingKind
    title: str
    detail: str
    recommendation: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    rubric_dimension: str | None = None
    speaker_labels: list[str] = Field(default_factory=list)


class FeedbackSectionResponse(BaseModel):
    summary: str
    strengths: list[FindingResponse] = Field(default_factory=list)
    improvements: list[FindingResponse] = Field(default_factory=list)
    score_components: list[ScoreComponentResponse] = Field(default_factory=list)
    limitations: list[dict[str, Any]] = Field(default_factory=list)


class MemberFeedbackResponse(BaseModel):
    user_id: UUID
    display_name: str
    speaker_labels: list[str]
    summary: str
    strengths: list[FindingResponse] = Field(default_factory=list)
    improvements: list[FindingResponse] = Field(default_factory=list)
    delivery_components: list[ScoreComponentResponse] = Field(default_factory=list)
    qa_feedback: FeedbackSectionResponse | None = None


class RubricRefResponse(BaseModel):
    id: str
    version: int


class EvaluationResponse(BaseModel):
    id: UUID
    practice_session_id: UUID
    analysis_attempt_id: UUID
    qa_round_id: UUID
    rubric: RubricRefResponse
    overall_score: float | None = None
    components: list[ScoreComponentResponse] = Field(default_factory=list)
    findings: list[FindingResponse] = Field(default_factory=list)
    team_feedback: FeedbackSectionResponse
    member_feedback: list[MemberFeedbackResponse] = Field(default_factory=list)
    limitations: list[dict[str, Any]] = Field(default_factory=list)
    reproducibility: dict[str, Any] = Field(default_factory=dict)


class ReportPayloadResponse(BaseModel):
    schema_version: int = 1
    report_id: UUID
    practice_session_id: UUID
    evaluation_id: UUID
    title: str
    executive_summary: str
    overall_score: float
    score_components: list[ScoreComponentResponse] = Field(default_factory=list)
    team_feedback: FeedbackSectionResponse
    member_feedback: list[MemberFeedbackResponse] = Field(default_factory=list)
    transcript_timeline: list[dict[str, Any]] = Field(default_factory=list)
    document_alignment: list[dict[str, Any]] = Field(default_factory=list)
    qa_review: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[dict[str, Any]] = Field(default_factory=list)
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime


class ReportExportResponse(BaseModel):
    id: UUID
    report_id: UUID
    practice_session_id: UUID
    format: ReportExportFormat = ReportExportFormat.PDF
    status: ReportExportStatus
    asset_version_id: UUID | None = None
    failure: SafeFailure | None = None
    created_at: datetime
    completed_at: datetime | None = None
