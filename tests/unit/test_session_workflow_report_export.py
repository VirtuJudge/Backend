from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.application.services.asset_store import AssetStore
from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.report import (
    Evaluation,
    FeedbackSection,
    Finding,
    MemberFeedback,
    Report,
    ReportExport,
    ScoreComponent,
)
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.report import (
    FindingKind,
    ReportExportFormat,
    ReportExportStatus,
    ScoreLabel,
    ScoreStatus,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    ReportExportNotReadyError,
    ReportNotReadyError,
    UnauthorizedSessionAction,
)
from app.infrastructure.pdf.pdf_generator import PyPdfReportGenerator
from tests.support import (
    FakeAssetRepository,
    FakeObjectStorage,
    FakeReportRepository,
    FakeUnitOfWork,
)


class _FakeSessionsRepo:
    def __init__(self, session: PracticeSession) -> None:
        self.sessions: dict[UUID, PracticeSession] = {session.id: session}

    async def get_by_id(self, session_id: UUID) -> PracticeSession | None:
        return self.sessions.get(session_id)

    async def update(
        self, session: PracticeSession, *, expected_version: int | None = None
    ) -> PracticeSession:
        self.sessions[session.id] = session
        return session


class _FakeProjectsRepo:
    def __init__(self, project_id: UUID, member_id: UUID) -> None:
        self.project_id = project_id
        self.member_id = member_id

    async def is_member(self, project_id: UUID, user_id: UUID) -> bool:
        return bool(project_id == self.project_id and user_id == self.member_id)


class _FakeIdempotencyRepo:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, UUID, str, str], Any] = {}

    async def get(self, session_id: UUID, actor_id: UUID, operation: str, key: str) -> Any | None:
        return self.records.get((session_id, actor_id, operation, key))

    async def create(self, record: Any) -> Any:
        key = (record.session_id, record.actor_id, record.operation, record.idempotency_key)
        self.records[key] = record
        return record


def _create_sample_data() -> tuple[PracticeSession, Evaluation, Report, UUID, UUID]:
    session_id = uuid4()
    project_id = uuid4()
    actor_id = uuid4()
    eval_id = uuid4()
    now = datetime.now(UTC)

    session = PracticeSession(
        id=session_id,
        project_id=project_id,
        created_by=actor_id,
        name="Team Practice Session",
        status=SessionStatus.COMPLETED,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
        started_at=now,
        completed_at=now,
        cancelled_at=None,
    )

    comp = ScoreComponent(
        dimension="clarity",
        status=ScoreStatus.SCORED,
        configured_weight=1.0,
        normalized_score=0.9,
        display_score=90,
        label=ScoreLabel.STRONG,
        effective_weight=1.0,
    )
    finding = Finding(
        id="f-1",
        kind=FindingKind.STRENGTH,
        title="Pacing",
        detail="Delivery was crisp and well-paced.",
    )
    team_fb = FeedbackSection(
        summary="Great job overall.",
        strengths=[finding],
        improvements=[],
        score_components=[comp],
    )
    member_fb = MemberFeedback(
        user_id=actor_id,
        display_name="Alice",
        speaker_labels=["SPEAKER_00"],
        summary="Great vocal pacing.",
        strengths=[finding],
        improvements=[],
        delivery_components=[comp],
    )
    evaluation = Evaluation(
        id=eval_id,
        practice_session_id=session_id,
        analysis_attempt_id=uuid4(),
        qa_round_id=uuid4(),
        rubric_id="rubric_standard_v1",
        rubric_version=1,
        overall_score=0.9,
        components=[comp],
        findings=[finding],
        team_feedback=team_fb,
        member_feedback=[member_fb],
        created_at=now,
    )
    report = Report(
        id=uuid4(),
        practice_session_id=session_id,
        evaluation_id=eval_id,
        title="Demo Day Report",
        executive_summary="Executive summary text.",
        overall_score=0.9,
        score_components=[comp],
        team_feedback=team_fb,
        member_feedback=[member_fb],
        generated_at=now,
        updated_at=now,
    )
    return session, evaluation, report, actor_id, project_id


@pytest.mark.asyncio
async def test_export_report_pdf_creates_verified_asset_and_ready_export() -> None:
    session, evaluation, report, actor_id, project_id = _create_sample_data()

    reports_repo = FakeReportRepository()
    await reports_repo.save_evaluation(evaluation)
    await reports_repo.save_report(report)

    uow = FakeUnitOfWork(reports=reports_repo)
    uow.sessions = _FakeSessionsRepo(session)
    uow.projects = _FakeProjectsRepo(project_id, actor_id)
    uow.idempotency = _FakeIdempotencyRepo()

    storage = FakeObjectStorage()
    asset_repo = FakeAssetRepository()
    team_id = uuid4()
    from app.domain.project import Project

    asset_repo.projects[project_id] = Project(
        id=project_id,
        team_id=team_id,
        name="Test Project",
        description=None,
        created_at=datetime.now(UTC),
        version=1,
    )
    asset_repo.team_members.add((team_id, actor_id))

    asset_store = AssetStore(
        repository=asset_repo,
        storage=storage,
        document_verifier=MagicMock(),
    )

    workflow = SessionWorkflow(
        uow=uow,
        pdf_generator=PyPdfReportGenerator(),
    )

    export = await workflow.export_report_pdf(
        session_id=session.id,
        actor_id=actor_id,
        asset_store=asset_store,
        idempotency_key="pdf-export-1",
    )

    assert export.status is ReportExportStatus.READY
    assert export.format is ReportExportFormat.PDF
    assert export.asset_version_id is not None
    assert export.completed_at is not None

    # Verify asset was saved and marked verified
    saved_version = await asset_repo.get_version(export.asset_version_id)
    assert saved_version is not None
    assert saved_version.state == "verified"
    assert saved_version.media_type == "application/pdf"
    assert saved_version.storage_key in storage.objects

    # Calling with same idempotency key returns the existing export
    second_export = await workflow.export_report_pdf(
        session_id=session.id,
        actor_id=actor_id,
        asset_store=asset_store,
        idempotency_key="pdf-export-1",
    )
    assert second_export.id == export.id

    # Verify download intent creation
    intent = await workflow.create_export_download_intent(
        export_id=export.id,
        actor_id=actor_id,
        asset_store=asset_store,
    )
    assert intent.asset_version_id == export.asset_version_id
    assert intent.media_type == "application/pdf"
    assert "https://" in intent.download_url


@pytest.mark.asyncio
async def test_export_report_pdf_not_ready_raises() -> None:
    session, _, _, actor_id, project_id = _create_sample_data()

    reports_repo = FakeReportRepository()
    uow = FakeUnitOfWork(reports=reports_repo)
    uow.sessions = _FakeSessionsRepo(session)
    uow.projects = _FakeProjectsRepo(project_id, actor_id)

    asset_store = MagicMock()
    workflow = SessionWorkflow(uow=uow)

    with pytest.raises(ReportNotReadyError):
        await workflow.export_report_pdf(
            session_id=session.id,
            actor_id=actor_id,
            asset_store=asset_store,
        )


@pytest.mark.asyncio
async def test_get_report_and_evaluation() -> None:
    session, evaluation, report, actor_id, project_id = _create_sample_data()

    reports_repo = FakeReportRepository()
    await reports_repo.save_evaluation(evaluation)
    await reports_repo.save_report(report)

    uow = FakeUnitOfWork(reports=reports_repo)
    uow.sessions = _FakeSessionsRepo(session)
    uow.projects = _FakeProjectsRepo(project_id, actor_id)

    workflow = SessionWorkflow(uow=uow)

    fetched_report = await workflow.get_report(session.id, actor_id)
    assert fetched_report.id == report.id
    assert fetched_report.overall_score == 0.9

    fetched_eval = await workflow.get_evaluation(session.id, actor_id)
    assert fetched_eval.id == evaluation.id
    assert fetched_eval.overall_score == 0.9


@pytest.mark.asyncio
async def test_get_report_unauthorized_raises() -> None:
    session, evaluation, report, actor_id, project_id = _create_sample_data()
    outsider_id = uuid4()

    reports_repo = FakeReportRepository()
    await reports_repo.save_report(report)

    uow = FakeUnitOfWork(reports=reports_repo)
    uow.sessions = _FakeSessionsRepo(session)
    uow.projects = _FakeProjectsRepo(project_id, actor_id)

    workflow = SessionWorkflow(uow=uow)

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.get_report(session.id, outsider_id)


@pytest.mark.asyncio
async def test_create_export_download_intent_not_ready_raises() -> None:
    session, _, _, actor_id, project_id = _create_sample_data()

    queued_export = ReportExport(
        id=uuid4(),
        report_id=uuid4(),
        practice_session_id=session.id,
        format=ReportExportFormat.PDF,
        status=ReportExportStatus.QUEUED,
        asset_version_id=None,
    )

    reports_repo = FakeReportRepository()
    await reports_repo.save_report_export(queued_export)

    uow = FakeUnitOfWork(reports=reports_repo)
    uow.sessions = _FakeSessionsRepo(session)
    uow.projects = _FakeProjectsRepo(project_id, actor_id)

    asset_store = MagicMock()
    workflow = SessionWorkflow(uow=uow)

    with pytest.raises(ReportExportNotReadyError):
        await workflow.create_export_download_intent(
            export_id=queued_export.id,
            actor_id=actor_id,
            asset_store=asset_store,
        )
