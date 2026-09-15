from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.session_workflow.enums.report import (
    EvidenceType,
    FindingKind,
    ReportExportFormat,
    ReportExportStatus,
    ScoreLabel,
    ScoreStatus,
)


@dataclass(slots=True)
class EvidenceReference:
    id: str
    type: EvidenceType
    source: dict[str, Any]
    excerpt: str | None = None


@dataclass(slots=True)
class Finding:
    id: str
    kind: FindingKind
    title: str
    detail: str
    recommendation: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    rubric_dimension: str | None = None
    speaker_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoreComponent:
    dimension: str
    status: ScoreStatus
    configured_weight: float
    normalized_score: float | None = None
    display_score: int | None = None
    label: ScoreLabel | None = None
    effective_weight: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    rationale: str | None = None
    limitation_code: str | None = None


@dataclass(slots=True)
class FeedbackSection:
    summary: str
    strengths: list[Finding] = field(default_factory=list)
    improvements: list[Finding] = field(default_factory=list)
    score_components: list[ScoreComponent] = field(default_factory=list)
    limitations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class MemberFeedback:
    user_id: UUID
    display_name: str
    speaker_labels: list[str]
    summary: str
    strengths: list[Finding] = field(default_factory=list)
    improvements: list[Finding] = field(default_factory=list)
    delivery_components: list[ScoreComponent] = field(default_factory=list)
    qa_feedback: FeedbackSection | None = None


@dataclass(slots=True)
class Evaluation:
    id: UUID
    practice_session_id: UUID
    analysis_attempt_id: UUID
    qa_round_id: UUID
    rubric_id: str
    rubric_version: int
    overall_score: float
    components: list[ScoreComponent]
    findings: list[Finding]
    team_feedback: FeedbackSection
    member_feedback: list[MemberFeedback]
    limitations: list[dict[str, Any]] = field(default_factory=list)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class Report:
    id: UUID
    practice_session_id: UUID
    evaluation_id: UUID
    title: str
    executive_summary: str
    overall_score: float
    score_components: list[ScoreComponent]
    team_feedback: FeedbackSection
    member_feedback: list[MemberFeedback]
    markdown: str = ""
    transcript_timeline: list[dict[str, Any]] = field(default_factory=list)
    document_alignment: list[dict[str, Any]] = field(default_factory=list)
    qa_review: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    limitations: list[dict[str, Any]] = field(default_factory=list)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class ReportExport:
    id: UUID
    report_id: UUID
    practice_session_id: UUID
    format: ReportExportFormat = ReportExportFormat.PDF
    status: ReportExportStatus = ReportExportStatus.QUEUED
    asset_version_id: UUID | None = None
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
