import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ai_jobs import AIJobs
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
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.qa import QARoundState, QuestionKind, QuestionState
from app.domain.session_workflow.enums.report import (
    FindingKind,
    ReportExportFormat,
    ReportExportStatus,
    ScoreLabel,
    ScoreStatus,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    PracticeSessionModel,
    QARoundModel,
    QuestionModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fakes import FakeObjectStorage


async def _seed_session_ancestry(
    session: AsyncSession,
) -> tuple[PracticeSessionModel, AnalysisAttemptModel, QARoundModel]:
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    attempt_id = uuid4()
    presentation_asset_id = uuid4()
    presentation_version_id = uuid4()

    session.add_all(
        [
            UserModel(id=user_id, issuer="test", subject=str(user_id), created_at=now),
            TeamModel(id=team_id, name="Test Team", created_at=now),
        ]
    )
    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Test Project",
            description="Test Description",
            created_at=now,
        )
    )
    session.add(
        TeamMemberModel(
            id=uuid4(),
            team_id=team_id,
            user_id=user_id,
            role="owner",
            joined_at=now,
        )
    )
    await session.flush()

    session.add(
        AssetModel(
            id=presentation_asset_id,
            project_id=project_id,
            kind="presentation_video",
            file_name="presentation.mp4",
            created_by=user_id,
            created_at=now,
        )
    )
    await session.flush()

    session.add(
        AssetVersionModel(
            id=presentation_version_id,
            asset_id=presentation_asset_id,
            storage_key=f"uploads/{team_id}/{project_id}/presentation.mp4",
            file_name="presentation.mp4",
            declared_media_type="video/mp4",
            declared_size_bytes=1024,
            created_by=user_id,
            created_at=now,
            upload_expires_at=now,
        )
    )
    await session.flush()

    prac_session = PracticeSessionModel(
        id=session_id,
        project_id=project_id,
        created_by=user_id,
        status=SessionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )
    session.add(prac_session)
    await session.flush()

    session.add(
        SessionManifestModel(
            id=manifest_id,
            session_id=session_id,
            presentation_version_id=presentation_version_id,
            rubric_id="startup_pitch",
            rubric_version=1,
            snapshot={"version": 1},
            frozen_at=now,
        )
    )
    await session.flush()

    attempt = AnalysisAttemptModel(
        id=attempt_id,
        session_id=session_id,
        manifest_id=manifest_id,
        attempt_number=1,
        status=AnalysisAttemptStatus.COMPLETED,
        created_at=now,
    )
    session.add(attempt)
    await session.flush()

    qa_round = QARoundModel(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=attempt.id,
        state=QARoundState.COMPLETED,
        created_at=now,
        updated_at=now,
    )
    session.add(qa_round)
    await session.flush()

    return prac_session, attempt, qa_round


@pytest.mark.anyio
async def test_completing_qa_round_uploads_artifact_before_report_job_is_queued(
    async_db_session: AsyncSession,
) -> None:
    uow = SqlAlchemyUnitOfWork(async_db_session)
    practice_session, attempt, qa_round = await _seed_session_ancestry(async_db_session)
    now = datetime.now(UTC)
    question_id = uuid4()

    async_db_session.add(
        QuestionModel(
            id=question_id,
            qa_round_id=qa_round.id,
            practice_session_id=practice_session.id,
            kind=QuestionKind.PRIMARY,
            position=1,
            text="How will the team acquire customers?",
            reason="Tests the go-to-market plan.",
            rubric_dimension="business_reasoning",
            evidence_ids=["ev_01"],
            state=QuestionState.ACTIVE,
            created_at=now,
        )
    )
    await async_db_session.flush()
    practice_session.status = SessionStatus.QUESTIONS_IN_PROGRESS
    qa_round.state = QARoundState.IN_PROGRESS
    qa_round.current_question_id = question_id
    await async_db_session.flush()

    storage = FakeObjectStorage()
    queue = FakeAIJobQueue()
    service = AIJobs(uow, queue=queue, storage=storage)
    workflow = SessionWorkflow(uow, ai_jobs=service, queue=queue)
    await workflow.skip_answer(
        question_id=question_id,
        actor_id=practice_session.created_by,
        reason="Need more research.",
        idempotency_key="complete-qa-round",
    )

    expected_key = (
        f"projects/{practice_session.project_id}/sessions/{practice_session.id}/attempts/"
        f"{attempt.attempt_number}/qa.json"
    )
    uploaded = storage.objects[expected_key]
    uploaded_qa = json.loads(uploaded)
    assert uploaded_qa["qa_round_id"] == str(qa_round.id)
    assert uploaded_qa["questions"][0]["id"] == str(question_id)
    assert uploaded_qa["answers"][0]["status"] == "skipped"
    queued = queue.last_message
    assert queued is not None
    queued_artifact = queued.model_dump()["payload"]["qa_artifact"]
    assert queued_artifact["object_key"] == expected_key
    assert queued_artifact["checksum"] == f"sha256:{hashlib.sha256(uploaded).hexdigest()}"


@pytest.mark.anyio
async def test_report_repository_crud(async_db_session: AsyncSession) -> None:
    uow = SqlAlchemyUnitOfWork(async_db_session)
    prac_session, attempt, qa_round = await _seed_session_ancestry(async_db_session)
    user_id = uuid4()
    now = datetime.now(UTC)

    member_feedback = [
        MemberFeedback(
            user_id=user_id,
            display_name="Founder",
            speaker_labels=["SPEAKER_00"],
            summary="Strong presentation.",
            strengths=[
                Finding(
                    id="f1",
                    kind=FindingKind.STRENGTH,
                    title="Pacing",
                    detail="Great cadence.",
                    evidence_ids=["ev_1"],
                )
            ],
            improvements=[],
            delivery_components=[
                ScoreComponent(
                    dimension="delivery",
                    status=ScoreStatus.SCORED,
                    configured_weight=0.20,
                    normalized_score=0.85,
                    display_score=85,
                    label=ScoreLabel.STRONG,
                    evidence_ids=["ev_1"],
                    rationale="Fluent.",
                )
            ],
        )
    ]

    team_feedback = FeedbackSection(
        summary="Team worked well together.",
        strengths=[],
        improvements=[],
    )

    evaluation = Evaluation(
        id=uuid4(),
        practice_session_id=prac_session.id,
        analysis_attempt_id=attempt.id,
        qa_round_id=qa_round.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        overall_score=0.82,
        components=[
            ScoreComponent(
                dimension="q_and_a",
                status=ScoreStatus.SCORED,
                configured_weight=0.20,
                normalized_score=0.80,
                display_score=80,
                label=ScoreLabel.STRONG,
                evidence_ids=["ev_2"],
                rationale="Good answers.",
            )
        ],
        findings=[],
        team_feedback=team_feedback,
        member_feedback=member_feedback,
        limitations=[],
        reproducibility={"model": "test"},
        created_at=now,
    )

    # Save evaluation
    await uow.reports.save_evaluation(evaluation)
    await uow.commit()

    loaded_eval = await uow.reports.get_evaluation(evaluation.id)
    assert loaded_eval is not None
    assert loaded_eval.id == evaluation.id
    assert loaded_eval.practice_session_id == prac_session.id
    assert loaded_eval.overall_score == 0.82
    assert len(loaded_eval.member_feedback) == 1
    assert loaded_eval.member_feedback[0].user_id == user_id

    loaded_by_session = await uow.reports.get_evaluation_by_session(prac_session.id)
    assert loaded_by_session is not None
    assert loaded_by_session.id == evaluation.id

    # Save report
    report = Report(
        id=uuid4(),
        practice_session_id=prac_session.id,
        evaluation_id=evaluation.id,
        title="Demo Day Pitch",
        executive_summary="Excellent team performance.",
        overall_score=0.82,
        score_components=evaluation.components,
        team_feedback=team_feedback,
        member_feedback=member_feedback,
        generated_at=now,
        updated_at=now,
    )

    await uow.reports.save_report(report)
    await uow.commit()

    loaded_report = await uow.reports.get_report(report.id)
    assert loaded_report is not None
    assert loaded_report.id == report.id
    assert loaded_report.title == "Demo Day Pitch"
    assert loaded_report.evaluation_id == evaluation.id
    assert loaded_report.team_feedback.summary == "Team worked well together."

    loaded_report_by_session = await uow.reports.get_report_by_session(prac_session.id)
    assert loaded_report_by_session is not None
    assert loaded_report_by_session.id == report.id

    replacement_evaluation = replace(evaluation, id=uuid4(), overall_score=0.63)
    await uow.reports.save_evaluation(replacement_evaluation)
    await uow.commit()
    replacement_report = replace(
        report,
        evaluation_id=replacement_evaluation.id,
        overall_score=replacement_evaluation.overall_score,
        title="Rebuilt Demo Day Pitch",
    )
    await uow.reports.save_report(replacement_report)
    await uow.commit()

    rebuilt_report = await uow.reports.get_report_by_session(prac_session.id)
    assert rebuilt_report is not None
    assert rebuilt_report.id == report.id
    assert rebuilt_report.evaluation_id == replacement_evaluation.id
    assert rebuilt_report.overall_score == 0.63
    assert rebuilt_report.title == "Rebuilt Demo Day Pitch"

    # Save report export
    export = ReportExport(
        id=uuid4(),
        report_id=report.id,
        practice_session_id=prac_session.id,
        format=ReportExportFormat.PDF,
        status=ReportExportStatus.QUEUED,
        created_at=now,
    )
    await uow.reports.save_report_export(export)
    await uow.commit()

    loaded_export = await uow.reports.get_report_export(export.id)
    assert loaded_export is not None
    assert loaded_export.status is ReportExportStatus.QUEUED

    # Update report export
    export.status = ReportExportStatus.READY
    export.completed_at = datetime.now(UTC)
    await uow.reports.update_report_export(export)
    await uow.commit()

    reloaded_export = await uow.reports.get_report_export(export.id)
    assert reloaded_export is not None
    assert reloaded_export.status is ReportExportStatus.READY
    assert reloaded_export.completed_at is not None
