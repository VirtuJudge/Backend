from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_asset_store
from app.api.dependencies.session_workflow import get_session_workflow
from app.application.session_workflow import SessionWorkflow
from app.domain.asset import DownloadIntent
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
from app.domain.session_workflow.exceptions import (
    EvaluationNotReadyError,
    ReportExportNotFoundError,
    ReportExportNotReadyError,
    ReportNotReadyError,
    SessionNotFoundError,
    UnauthorizedSessionAction,
)
from app.domain.user import User
from app.main import create_app
from app.settings import Settings


def _create_user() -> User:
    return User(
        id=uuid4(),
        issuer="https://identity.example.com",
        subject="test-user",
        email="test@example.com",
        created_at=datetime.now(UTC),
        display_name="Test User",
    )


def _create_sample_report(session_id: UUID, evaluation_id: UUID) -> Report:
    now = datetime.now(UTC)
    comp = ScoreComponent(
        dimension="clarity",
        status=ScoreStatus.SCORED,
        configured_weight=1.0,
        normalized_score=0.85,
        display_score=85,
        label=ScoreLabel.STRONG,
        effective_weight=1.0,
    )
    finding = Finding(
        id="f-1",
        kind=FindingKind.STRENGTH,
        title="Clear pacing",
        detail="Presenter paced the delivery well.",
    )
    team_fb = FeedbackSection(
        summary="Solid team presentation.",
        strengths=[finding],
        improvements=[],
        score_components=[comp],
        limitations=[],
    )
    member_fb = MemberFeedback(
        user_id=uuid4(),
        display_name="Alice",
        speaker_labels=["SPEAKER_00"],
        summary="Good clarity and confidence.",
        strengths=[finding],
        improvements=[],
        delivery_components=[comp],
    )
    return Report(
        id=uuid4(),
        practice_session_id=session_id,
        evaluation_id=evaluation_id,
        title="Demo Day Report",
        executive_summary="Executive summary content.",
        overall_score=0.85,
        score_components=[comp],
        team_feedback=team_fb,
        member_feedback=[member_fb],
        markdown="# Demo Day Report\n\n## Summary\n\nThe team communicated clearly.",
        generated_at=now,
        updated_at=now,
    )


def _create_sample_evaluation(session_id: UUID) -> Evaluation:
    now = datetime.now(UTC)
    comp = ScoreComponent(
        dimension="clarity",
        status=ScoreStatus.SCORED,
        configured_weight=1.0,
        normalized_score=0.85,
        display_score=85,
        label=ScoreLabel.STRONG,
        effective_weight=1.0,
    )
    finding = Finding(
        id="f-1",
        kind=FindingKind.STRENGTH,
        title="Clear pacing",
        detail="Presenter paced the delivery well.",
    )
    team_fb = FeedbackSection(
        summary="Solid team presentation.",
        strengths=[finding],
        improvements=[],
        score_components=[comp],
        limitations=[],
    )
    member_fb = MemberFeedback(
        user_id=uuid4(),
        display_name="Alice",
        speaker_labels=["SPEAKER_00"],
        summary="Good clarity and confidence.",
        strengths=[finding],
        improvements=[],
        delivery_components=[comp],
    )
    return Evaluation(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=uuid4(),
        qa_round_id=uuid4(),
        rubric_id="rubric_standard_v1",
        rubric_version=1,
        overall_score=0.85,
        components=[comp],
        findings=[finding],
        team_feedback=team_fb,
        member_feedback=[member_fb],
        created_at=now,
    )


def _setup_app(
    workflow_mock: MagicMock | None = None, asset_store_mock: MagicMock | None = None
) -> tuple[Any, User]:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    user = _create_user()
    app.dependency_overrides[get_current_user] = lambda: user
    if workflow_mock is not None:
        app.dependency_overrides[get_session_workflow] = lambda: workflow_mock
    if asset_store_mock is not None:
        app.dependency_overrides[get_asset_store] = lambda: asset_store_mock
    return app, user


@pytest.mark.asyncio
async def test_get_session_report_success() -> None:
    session_id = uuid4()
    eval_id = uuid4()
    report = _create_sample_report(session_id, eval_id)

    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report = AsyncMock(return_value=report)

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/practice-sessions/{session_id}/report")

    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == str(report.id)
    assert data["overall_score"] == 0.85
    assert data["title"] == "Demo Day Report"
    assert data["markdown"] == report.markdown
    assert len(data["score_components"]) == 1
    assert len(data["member_feedback"]) == 1


@pytest.mark.asyncio
async def test_get_session_report_not_ready_409() -> None:
    session_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report = AsyncMock(side_effect=ReportNotReadyError("Report is not ready."))

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/practice-sessions/{session_id}/report")

    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "report_not_ready"


@pytest.mark.asyncio
async def test_get_session_report_session_not_found_404() -> None:
    session_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report = AsyncMock(side_effect=SessionNotFoundError("Session not found."))

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/practice-sessions/{session_id}/report")

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "not_found"


@pytest.mark.asyncio
async def test_get_session_evaluation_success() -> None:
    session_id = uuid4()
    evaluation = _create_sample_evaluation(session_id)

    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_evaluation = AsyncMock(return_value=evaluation)

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/practice-sessions/{session_id}/evaluation")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(evaluation.id)
    assert data["rubric"]["id"] == "rubric_standard_v1"
    assert data["overall_score"] == 0.85
    assert len(data["components"]) == 1


@pytest.mark.asyncio
async def test_get_session_evaluation_not_ready_409() -> None:
    session_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_evaluation = AsyncMock(
        side_effect=EvaluationNotReadyError("Evaluation is not ready.")
    )

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/practice-sessions/{session_id}/evaluation")

    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "evaluation_not_ready"


@pytest.mark.asyncio
async def test_export_session_report_pdf_202() -> None:
    session_id = uuid4()
    report_id = uuid4()
    export = ReportExport(
        id=uuid4(),
        report_id=report_id,
        practice_session_id=session_id,
        format=ReportExportFormat.PDF,
        status=ReportExportStatus.READY,
        asset_version_id=uuid4(),
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    workflow = MagicMock(spec=SessionWorkflow)
    workflow.export_report_pdf = AsyncMock(return_value=export)
    asset_store = MagicMock()

    app, _ = _setup_app(workflow_mock=workflow, asset_store_mock=asset_store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/report/pdf",
            headers={"Idempotency-Key": "test-idem-key"},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["id"] == str(export.id)
    assert data["status"] == "ready"
    assert data["format"] == "pdf"
    assert data["asset_version_id"] == str(export.asset_version_id)


@pytest.mark.asyncio
async def test_export_session_report_pdf_not_ready_409() -> None:
    session_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.export_report_pdf = AsyncMock(side_effect=ReportNotReadyError("Report is not ready."))
    asset_store = MagicMock()

    app, _ = _setup_app(workflow_mock=workflow, asset_store_mock=asset_store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/practice-sessions/{session_id}/report/pdf")

    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "report_not_ready"


@pytest.mark.asyncio
async def test_get_report_export_success() -> None:
    export_id = uuid4()
    export = ReportExport(
        id=export_id,
        report_id=uuid4(),
        practice_session_id=uuid4(),
        format=ReportExportFormat.PDF,
        status=ReportExportStatus.READY,
        asset_version_id=uuid4(),
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report_export = AsyncMock(return_value=export)

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/report-exports/{export_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(export_id)
    assert data["status"] == "ready"


@pytest.mark.asyncio
async def test_get_report_export_not_found_404() -> None:
    export_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report_export = AsyncMock(
        side_effect=ReportExportNotFoundError("Export not found.")
    )

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/report-exports/{export_id}")

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "not_found"


@pytest.mark.asyncio
async def test_get_report_export_forbidden_403() -> None:
    export_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.get_report_export = AsyncMock(
        side_effect=UnauthorizedSessionAction("Access forbidden.")
    )

    app, _ = _setup_app(workflow_mock=workflow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/report-exports/{export_id}")

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "forbidden"


@pytest.mark.asyncio
async def test_create_report_export_download_intent_success() -> None:
    export_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()
    intent = DownloadIntent(
        asset_id=asset_id,
        asset_version_id=version_id,
        download_url="https://storage.example.com/pdf",
        expires_at=datetime.now(UTC),
        media_type="application/pdf",
        size_bytes=1024,
        file_name="report.pdf",
    )

    workflow = MagicMock(spec=SessionWorkflow)
    workflow.create_export_download_intent = AsyncMock(return_value=intent)
    asset_store = MagicMock()

    app, _ = _setup_app(workflow_mock=workflow, asset_store_mock=asset_store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/report-exports/{export_id}/download-intents")

    assert response.status_code == 200
    data = response.json()
    assert data["asset_id"] == str(asset_id)
    assert data["asset_version_id"] == str(version_id)
    assert data["download_url"] == "https://storage.example.com/pdf"
    assert data["media_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_create_report_export_download_intent_not_ready_409() -> None:
    export_id = uuid4()
    workflow = MagicMock(spec=SessionWorkflow)
    workflow.create_export_download_intent = AsyncMock(
        side_effect=ReportExportNotReadyError("Export not ready.")
    )
    asset_store = MagicMock()

    app, _ = _setup_app(workflow_mock=workflow, asset_store_mock=asset_store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/report-exports/{export_id}/download-intents")

    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "export_not_ready"
