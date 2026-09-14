from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domain.session_workflow.enums.qa import QARoundState
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
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    PracticeSessionModel,
    QARoundModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)


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
