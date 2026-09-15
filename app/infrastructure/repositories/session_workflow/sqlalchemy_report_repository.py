from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.report_repository import ReportRepository
from app.domain.session_workflow.entities.report import (
    Evaluation,
    FeedbackSection,
    Finding,
    MemberFeedback,
    Report,
    ReportExport,
    ScoreComponent,
)
from app.domain.session_workflow.enums.report import (
    FindingKind,
    ReportExportFormat,
    ReportExportStatus,
    ScoreLabel,
    ScoreStatus,
)
from app.infrastructure.persistence.configurations.session_workflow.report_configuration import (
    EvaluationModel,
    ReportExportModel,
    ReportModel,
)


def _score_component_from_dict(data: dict[str, Any]) -> ScoreComponent:
    label_val = data.get("label")
    return ScoreComponent(
        dimension=str(data["dimension"]),
        status=ScoreStatus(data.get("status", ScoreStatus.SCORED.value)),
        configured_weight=float(data["configured_weight"]),
        normalized_score=(
            float(data["normalized_score"]) if data.get("normalized_score") is not None else None
        ),
        display_score=(
            int(data["display_score"]) if data.get("display_score") is not None else None
        ),
        label=ScoreLabel(label_val) if label_val else None,
        effective_weight=(
            float(data["effective_weight"]) if data.get("effective_weight") is not None else None
        ),
        evidence_ids=list(data.get("evidence_ids", [])),
        rationale=data.get("rationale"),
        limitation_code=data.get("limitation_code"),
    )


def _finding_from_dict(data: dict[str, Any]) -> Finding:
    return Finding(
        id=str(data["id"]),
        kind=FindingKind(data.get("kind", FindingKind.OBSERVATION.value)),
        title=str(data["title"]),
        detail=str(data["detail"]),
        recommendation=data.get("recommendation"),
        evidence_ids=list(data.get("evidence_ids", [])),
        rubric_dimension=data.get("rubric_dimension"),
        speaker_labels=list(data.get("speaker_labels", [])),
    )


def _feedback_section_from_dict(data: dict[str, Any]) -> FeedbackSection:
    return FeedbackSection(
        summary=str(data.get("summary", "")),
        strengths=[_finding_from_dict(f) for f in data.get("strengths", [])],
        improvements=[_finding_from_dict(f) for f in data.get("improvements", [])],
        score_components=[_score_component_from_dict(c) for c in data.get("score_components", [])],
        limitations=list(data.get("limitations", [])),
    )


def _member_feedback_from_dict(data: dict[str, Any]) -> MemberFeedback:
    qa_fb = data.get("qa_feedback")
    return MemberFeedback(
        user_id=UUID(str(data["user_id"])),
        display_name=str(data["display_name"]),
        speaker_labels=list(data.get("speaker_labels", [])),
        summary=str(data.get("summary", "")),
        strengths=[_finding_from_dict(f) for f in data.get("strengths", [])],
        improvements=[_finding_from_dict(f) for f in data.get("improvements", [])],
        delivery_components=[
            _score_component_from_dict(c) for c in data.get("delivery_components", [])
        ],
        qa_feedback=_feedback_section_from_dict(qa_fb) if qa_fb else None,
    )


def _evaluation_from_model(model: EvaluationModel) -> Evaluation:
    payload = model.payload or {}
    components = [_score_component_from_dict(c) for c in payload.get("components", [])]
    findings = [_finding_from_dict(f) for f in payload.get("findings", [])]
    team_feedback = _feedback_section_from_dict(payload.get("team_feedback", {}))
    member_feedback = [_member_feedback_from_dict(m) for m in payload.get("member_feedback", [])]
    return Evaluation(
        id=model.id,
        practice_session_id=model.practice_session_id,
        analysis_attempt_id=model.analysis_attempt_id,
        qa_round_id=model.qa_round_id,
        rubric_id=model.rubric_id,
        rubric_version=model.rubric_version,
        overall_score=model.overall_score,
        components=components,
        findings=findings,
        team_feedback=team_feedback,
        member_feedback=member_feedback,
        limitations=list(payload.get("limitations", [])),
        reproducibility=dict(payload.get("reproducibility", {})),
        created_at=model.created_at,
    )


def _report_from_model(model: ReportModel) -> Report:
    payload = model.payload or {}
    components = [_score_component_from_dict(c) for c in payload.get("score_components", [])]
    team_feedback = _feedback_section_from_dict(payload.get("team_feedback", {}))
    member_feedback = [_member_feedback_from_dict(m) for m in payload.get("member_feedback", [])]
    return Report(
        id=model.id,
        practice_session_id=model.practice_session_id,
        evaluation_id=model.evaluation_id,
        title=model.title,
        executive_summary=model.executive_summary,
        overall_score=model.overall_score,
        score_components=components,
        team_feedback=team_feedback,
        member_feedback=member_feedback,
        transcript_timeline=list(payload.get("transcript_timeline", [])),
        document_alignment=list(payload.get("document_alignment", [])),
        qa_review=list(payload.get("qa_review", [])),
        recommendations=list(payload.get("recommendations", [])),
        limitations=list(payload.get("limitations", [])),
        reproducibility=dict(payload.get("reproducibility", {})),
        generated_at=model.created_at,
        updated_at=model.updated_at,
    )


def _score_component_to_dict(c: ScoreComponent) -> dict[str, Any]:
    return {
        "dimension": c.dimension,
        "status": c.status.value,
        "configured_weight": c.configured_weight,
        "normalized_score": c.normalized_score,
        "display_score": c.display_score,
        "label": c.label.value if c.label else None,
        "effective_weight": c.effective_weight,
        "evidence_ids": c.evidence_ids,
        "rationale": c.rationale,
        "limitation_code": c.limitation_code,
    }


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        "id": f.id,
        "kind": f.kind.value,
        "title": f.title,
        "detail": f.detail,
        "recommendation": f.recommendation,
        "evidence_ids": f.evidence_ids,
        "rubric_dimension": f.rubric_dimension,
        "speaker_labels": f.speaker_labels,
    }


def _feedback_section_to_dict(section: FeedbackSection) -> dict[str, Any]:
    return {
        "summary": section.summary,
        "strengths": [_finding_to_dict(f) for f in section.strengths],
        "improvements": [_finding_to_dict(f) for f in section.improvements],
        "score_components": [_score_component_to_dict(c) for c in section.score_components],
        "limitations": section.limitations,
    }


def _member_feedback_to_dict(m: MemberFeedback) -> dict[str, Any]:
    return {
        "user_id": str(m.user_id),
        "display_name": m.display_name,
        "speaker_labels": m.speaker_labels,
        "summary": m.summary,
        "strengths": [_finding_to_dict(f) for f in m.strengths],
        "improvements": [_finding_to_dict(f) for f in m.improvements],
        "delivery_components": [_score_component_to_dict(c) for c in m.delivery_components],
        "qa_feedback": _feedback_section_to_dict(m.qa_feedback) if m.qa_feedback else None,
    }


def _evaluation_payload(evaluation: Evaluation) -> dict[str, Any]:
    return {
        "components": [_score_component_to_dict(c) for c in evaluation.components],
        "findings": [_finding_to_dict(f) for f in evaluation.findings],
        "team_feedback": _feedback_section_to_dict(evaluation.team_feedback),
        "member_feedback": [_member_feedback_to_dict(m) for m in evaluation.member_feedback],
        "limitations": evaluation.limitations,
        "reproducibility": evaluation.reproducibility,
    }


def _report_payload(report: Report) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_id": str(report.id),
        "practice_session_id": str(report.practice_session_id),
        "evaluation_id": str(report.evaluation_id),
        "title": report.title,
        "executive_summary": report.executive_summary,
        "overall_score": report.overall_score,
        "score_components": [_score_component_to_dict(c) for c in report.score_components],
        "team_feedback": _feedback_section_to_dict(report.team_feedback),
        "member_feedback": [_member_feedback_to_dict(m) for m in report.member_feedback],
        "transcript_timeline": report.transcript_timeline,
        "document_alignment": report.document_alignment,
        "qa_review": report.qa_review,
        "recommendations": report.recommendations,
        "limitations": report.limitations,
        "reproducibility": report.reproducibility,
        "generated_at": report.generated_at.isoformat(),
    }


def _export_from_model(model: ReportExportModel) -> ReportExport:
    return ReportExport(
        id=model.id,
        report_id=model.report_id,
        practice_session_id=model.practice_session_id,
        format=ReportExportFormat(model.format),
        status=ReportExportStatus(model.status),
        asset_version_id=model.asset_version_id,
        failure_reason=model.failure_reason,
        created_at=model.created_at,
        completed_at=model.completed_at,
    )


class SqlAlchemyReportRepository(ReportRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_evaluation(self, evaluation: Evaluation) -> None:
        model = EvaluationModel(
            id=evaluation.id,
            practice_session_id=evaluation.practice_session_id,
            analysis_attempt_id=evaluation.analysis_attempt_id,
            qa_round_id=evaluation.qa_round_id,
            rubric_id=evaluation.rubric_id,
            rubric_version=evaluation.rubric_version,
            overall_score=evaluation.overall_score,
            payload=_evaluation_payload(evaluation),
            created_at=evaluation.created_at,
        )
        await self._session.merge(model)
        await self._session.flush()

    async def get_evaluation(self, evaluation_id: UUID) -> Evaluation | None:
        stmt = select(EvaluationModel).where(EvaluationModel.id == evaluation_id)
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _evaluation_from_model(model) if model else None

    async def get_evaluation_by_session(self, session_id: UUID) -> Evaluation | None:
        stmt = (
            select(EvaluationModel)
            .where(EvaluationModel.practice_session_id == session_id)
            .order_by(EvaluationModel.created_at.desc())
            .limit(1)
        )
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _evaluation_from_model(model) if model else None

    async def save_report(self, report: Report) -> None:
        model = ReportModel(
            id=report.id,
            practice_session_id=report.practice_session_id,
            evaluation_id=report.evaluation_id,
            title=report.title,
            executive_summary=report.executive_summary,
            overall_score=report.overall_score,
            payload=_report_payload(report),
            created_at=report.generated_at,
            updated_at=report.updated_at,
        )
        await self._session.merge(model)
        await self._session.flush()

    async def get_report(self, report_id: UUID) -> Report | None:
        stmt = select(ReportModel).where(ReportModel.id == report_id)
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _report_from_model(model) if model else None

    async def get_report_by_session(self, session_id: UUID) -> Report | None:
        stmt = select(ReportModel).where(ReportModel.practice_session_id == session_id)
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _report_from_model(model) if model else None

    async def save_report_export(self, export: ReportExport) -> None:
        model = ReportExportModel(
            id=export.id,
            report_id=export.report_id,
            practice_session_id=export.practice_session_id,
            format=export.format.value,
            status=export.status.value,
            asset_version_id=export.asset_version_id,
            failure_reason=export.failure_reason,
            created_at=export.created_at,
            completed_at=export.completed_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_report_export(self, export_id: UUID) -> ReportExport | None:
        stmt = select(ReportExportModel).where(ReportExportModel.id == export_id)
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _export_from_model(model) if model else None

    async def get_report_export_by_session(self, session_id: UUID) -> ReportExport | None:
        stmt = (
            select(ReportExportModel)
            .where(ReportExportModel.practice_session_id == session_id)
            .order_by(ReportExportModel.created_at.desc())
        )
        res = await self._session.execute(stmt)
        model = res.scalars().first()
        return _export_from_model(model) if model else None

    async def update_report_export(self, export: ReportExport) -> None:
        stmt = select(ReportExportModel).where(ReportExportModel.id == export.id)
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        if model is not None:
            model.status = export.status.value
            model.asset_version_id = export.asset_version_id
            model.failure_reason = export.failure_reason
            model.completed_at = export.completed_at
            await self._session.flush()
