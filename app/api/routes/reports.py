from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from app.api.correlation import get_correlation_id
from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_asset_store
from app.api.dependencies.session_workflow import get_session_workflow
from app.api.errors import handle_asset_error, problem_response
from app.api.schemas.asset import DownloadIntentResponse
from app.api.schemas.report import (
    EvaluationResponse,
    FeedbackSectionResponse,
    FindingResponse,
    MemberFeedbackResponse,
    ReportExportResponse,
    ReportPayloadResponse,
    RubricRefResponse,
    SafeFailure,
    ScoreComponentResponse,
)
from app.api.schemas.session_practice import ProblemDetails
from app.application.services.asset_store import AssetStore
from app.application.session_workflow import SessionWorkflow
from app.domain.asset import AssetDomainError
from app.domain.session_workflow.entities.report import (
    Evaluation,
    FeedbackSection,
    Finding,
    MemberFeedback,
    Report,
    ReportExport,
    ScoreComponent,
)
from app.domain.session_workflow.exceptions import (
    EvaluationNotReadyError,
    IdempotencyConflict,
    ReportExportNotFoundError,
    ReportExportNotReadyError,
    ReportNotReadyError,
    SessionNotFoundError,
    UnauthorizedSessionAction,
)
from app.domain.user import User

router = APIRouter(prefix="/api/v1", tags=["Reports"])


def _problem_response_doc(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": ProblemDetails.model_json_schema()}},
    }


REPORT_COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: _problem_response_doc("The caller is not a member of the session project."),
    404: _problem_response_doc("The resource was not found."),
    409: _problem_response_doc("The command conflicts with current state."),
    422: _problem_response_doc("The request parameters failed validation."),
}


def _score_component(component: ScoreComponent) -> ScoreComponentResponse:
    return ScoreComponentResponse(
        dimension=component.dimension,
        status=component.status,
        configured_weight=component.configured_weight,
        normalized_score=component.normalized_score,
        display_score=component.display_score,
        label=component.label,
        effective_weight=component.effective_weight,
        evidence_ids=component.evidence_ids,
        rationale=component.rationale,
        limitation_code=component.limitation_code,
    )


def _finding(finding: Finding) -> FindingResponse:
    return FindingResponse(
        id=finding.id,
        kind=finding.kind,
        title=finding.title,
        detail=finding.detail,
        recommendation=finding.recommendation,
        evidence_ids=finding.evidence_ids,
        rubric_dimension=finding.rubric_dimension,
        speaker_labels=finding.speaker_labels,
    )


def _feedback_section(section: FeedbackSection) -> FeedbackSectionResponse:
    return FeedbackSectionResponse(
        summary=section.summary,
        strengths=[_finding(f) for f in section.strengths],
        improvements=[_finding(f) for f in section.improvements],
        score_components=[_score_component(c) for c in section.score_components],
        limitations=section.limitations,
    )


def _member_feedback(member: MemberFeedback) -> MemberFeedbackResponse:
    return MemberFeedbackResponse(
        user_id=member.user_id,
        display_name=member.display_name,
        speaker_labels=member.speaker_labels,
        summary=member.summary,
        strengths=[_finding(f) for f in member.strengths],
        improvements=[_finding(f) for f in member.improvements],
        delivery_components=[_score_component(c) for c in member.delivery_components],
        qa_feedback=_feedback_section(member.qa_feedback) if member.qa_feedback else None,
    )


def _evaluation(evaluation: Evaluation) -> EvaluationResponse:
    return EvaluationResponse(
        id=evaluation.id,
        practice_session_id=evaluation.practice_session_id,
        analysis_attempt_id=evaluation.analysis_attempt_id,
        qa_round_id=evaluation.qa_round_id,
        rubric=RubricRefResponse(id=evaluation.rubric_id, version=evaluation.rubric_version),
        overall_score=evaluation.overall_score,
        components=[_score_component(c) for c in evaluation.components],
        findings=[_finding(f) for f in evaluation.findings],
        team_feedback=_feedback_section(evaluation.team_feedback),
        member_feedback=[_member_feedback(m) for m in evaluation.member_feedback],
        limitations=evaluation.limitations,
        reproducibility=evaluation.reproducibility,
    )


def _report(report: Report) -> ReportPayloadResponse:
    return ReportPayloadResponse(
        schema_version=1,
        report_id=report.id,
        practice_session_id=report.practice_session_id,
        evaluation_id=report.evaluation_id,
        title=report.title,
        executive_summary=report.executive_summary,
        overall_score=report.overall_score,
        score_components=[_score_component(c) for c in report.score_components],
        team_feedback=_feedback_section(report.team_feedback),
        member_feedback=[_member_feedback(m) for m in report.member_feedback],
        transcript_timeline=report.transcript_timeline,
        document_alignment=report.document_alignment,
        qa_review=report.qa_review,
        recommendations=report.recommendations,
        limitations=report.limitations,
        reproducibility=report.reproducibility,
        generated_at=report.generated_at,
    )


def _export(export: ReportExport, trace_id: str = "unknown") -> ReportExportResponse:
    failure = None
    if export.failure_reason:
        failure = SafeFailure(
            code="export_failed",
            message=export.failure_reason,
            retryable=True,
            trace_id=trace_id,
        )
    return ReportExportResponse(
        id=export.id,
        report_id=export.report_id,
        practice_session_id=export.practice_session_id,
        format=export.format,
        status=export.status,
        asset_version_id=export.asset_version_id,
        failure=failure,
        created_at=export.created_at,
        completed_at=export.completed_at,
    )


def _report_error(error: Exception, request: Request) -> Any:
    if isinstance(error, UnauthorizedSessionAction):
        return problem_response(403, "forbidden", "Forbidden", str(error), request.url.path)
    if isinstance(error, (SessionNotFoundError, ReportExportNotFoundError)):
        return problem_response(
            404,
            "not_found",
            "Resource not found",
            "The requested resource was not found.",
            request.url.path,
        )
    if isinstance(error, EvaluationNotReadyError):
        return problem_response(
            409,
            "evaluation_not_ready",
            "Evaluation not ready",
            str(error),
            request.url.path,
        )
    if isinstance(error, ReportNotReadyError):
        return problem_response(
            409,
            "report_not_ready",
            "Report not ready",
            str(error),
            request.url.path,
        )
    if isinstance(error, ReportExportNotReadyError):
        return problem_response(
            409,
            "export_not_ready",
            "Export not ready",
            str(error),
            request.url.path,
        )
    if isinstance(error, IdempotencyConflict):
        return problem_response(
            409,
            "idempotency_conflict",
            "Idempotency conflict",
            str(error),
            request.url.path,
        )
    if isinstance(error, AssetDomainError):
        return handle_asset_error(error, request.url.path)
    raise error


@router.get(
    "/practice-sessions/{session_id}/evaluation",
    response_model=EvaluationResponse,
    responses=REPORT_COMMON_RESPONSES,
)
async def get_session_evaluation(
    session_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        evaluation = await workflow.get_evaluation(session_id, current_user.id)
    except Exception as error:
        return _report_error(error, request)
    return _evaluation(evaluation)


@router.get(
    "/practice-sessions/{session_id}/report",
    response_model=ReportPayloadResponse,
    responses=REPORT_COMMON_RESPONSES,
)
async def get_session_report(
    session_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        report = await workflow.get_report(session_id, current_user.id)
    except Exception as error:
        return _report_error(error, request)
    return _report(report)


@router.post(
    "/practice-sessions/{session_id}/report/pdf",
    response_model=ReportExportResponse,
    status_code=202,
    responses=REPORT_COMMON_RESPONSES,
)
async def export_session_report_pdf(
    session_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    asset_store: AssetStore = Depends(get_asset_store),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    try:
        export = await workflow.export_report_pdf(
            session_id=session_id,
            actor_id=current_user.id,
            asset_store=asset_store,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        return _report_error(error, request)
    trace_id = get_correlation_id() or "unknown"
    return _export(export, trace_id)


@router.get(
    "/report-exports/{export_id}",
    response_model=ReportExportResponse,
    responses=REPORT_COMMON_RESPONSES,
)
async def get_report_export(
    export_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        export = await workflow.get_report_export(export_id, current_user.id)
    except Exception as error:
        return _report_error(error, request)
    trace_id = get_correlation_id() or "unknown"
    return _export(export, trace_id)


@router.post(
    "/report-exports/{export_id}/download-intents",
    response_model=DownloadIntentResponse,
    responses=REPORT_COMMON_RESPONSES,
)
async def create_report_export_download_intent(
    export_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    asset_store: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        intent = await workflow.create_export_download_intent(
            export_id=export_id,
            actor_id=current_user.id,
            asset_store=asset_store,
        )
    except Exception as error:
        return _report_error(error, request)
    return DownloadIntentResponse(
        asset_id=intent.asset_id,
        asset_version_id=intent.asset_version_id,
        download_url=intent.download_url,
        expires_at=intent.expires_at,
        media_type=intent.media_type,
        size_bytes=intent.size_bytes,
        file_name=intent.file_name,
    )
