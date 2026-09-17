from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.enums.stage_status import StageStatus
from app.domain.session_workflow.enums.stage_type import StageType
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetUploadIdempotencyModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.project_erasure_request import (
    ProjectErasureRequestModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AIJobModel,
    AnalysisAttemptModel,
    AnalysisStageModel,
    AnswerModel,
    EvaluationModel,
    PracticeSessionModel,
    QARoundModel,
    QuestionModel,
    ReportExportModel,
    ReportModel,
    SessionCommandIdempotencyModel,
    SessionManifestDocumentModel,
    SessionManifestModel,
    SpeakerMappingModel,
)
from app.infrastructure.repositories.session_workflow import (
    SqlAlchemyPracticeSessionRepository,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import (
    SqlAlchemyAssetRepository,
)
from app.infrastructure.repositories.sqlalchemy_project_repository import (
    SqlAlchemyProjectRepository,
)


async def _scalar_exists(session: AsyncSession, statement: Any) -> bool:
    res = await session.scalars(statement)
    return res.first() is not None


async def _setup_test_hierarchy(
    session: AsyncSession,
) -> tuple[UserModel, TeamModel, TeamMemberModel, ProjectModel, AssetVersionModel]:
    now = datetime.now(UTC)
    user = UserModel(
        id=uuid4(),
        issuer="https://auth.example.com",
        subject="test-subject",
        display_name="Test Owner",
        created_at=now,
    )
    team = TeamModel(id=uuid4(), name="Test Team", created_at=now)
    session.add_all([user, team])
    await session.flush()

    member = TeamMemberModel(
        id=uuid4(),
        team_id=team.id,
        user_id=user.id,
        role="owner",
        joined_at=now,
    )
    project = ProjectModel(
        id=uuid4(),
        team_id=team.id,
        name="Test Project",
        description="A test project",
        created_at=now,
    )
    session.add_all([member, project])
    await session.flush()

    asset = AssetModel(
        id=uuid4(),
        project_id=project.id,
        kind="presentation_video",
        state="verified",
        file_name="presentation.mp4",
        created_by=user.id,
        created_at=now,
    )
    session.add(asset)
    await session.flush()

    asset_version = AssetVersionModel(
        id=uuid4(),
        asset_id=asset.id,
        version_number=1,
        state="verified",
        storage_key=f"projects/{project.id}/assets/{asset.id}/v1.mp4",
        file_name="presentation.mp4",
        declared_media_type="video/mp4",
        declared_size_bytes=1000,
        created_by=user.id,
        created_at=now,
        upload_expires_at=now,
    )
    session.add(asset_version)
    asset.current_version_id = asset_version.id
    await session.flush()

    return user, team, member, project, asset_version


@pytest.mark.anyio
async def test_practice_session_delete_removes_all_related_entities(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    user, team, member, project, asset_version = await _setup_test_hierarchy(session)
    now = datetime.now(UTC)

    # 1. Practice session
    practice_session = PracticeSessionModel(
        id=uuid4(),
        name="Full Test Session",
        project_id=project.id,
        created_by=user.id,
        status=SessionStatus.READY,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
    )
    session.add(practice_session)
    await session.flush()
    session_id = practice_session.id

    # 2. Manifest and document
    manifest = SessionManifestModel(
        id=uuid4(),
        session_id=session_id,
        presentation_version_id=asset_version.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        frozen_at=now,
    )
    session.add(manifest)
    await session.flush()

    manifest_doc = SessionManifestDocumentModel(
        id=uuid4(),
        manifest_id=manifest.id,
        document_version_id=asset_version.id,
        created_at=now,
    )
    session.add(manifest_doc)

    # 3. Analysis attempt, stage, speaker mapping, AI job
    attempt = AnalysisAttemptModel(
        id=uuid4(),
        session_id=session_id,
        manifest_id=manifest.id,
        attempt_number=1,
        status=AnalysisAttemptStatus.COMPLETED,
        created_at=now,
        version=1,
    )
    session.add(attempt)
    await session.flush()

    stage = AnalysisStageModel(
        id=uuid4(),
        attempt_id=attempt.id,
        stage=StageType.TRANSCRIPTION,
        status=StageStatus.COMPLETED,
        progress=100,
    )
    speaker_map = SpeakerMappingModel(
        id=uuid4(),
        attempt_id=attempt.id,
        speaker_label="Speaker 1",
        member_id=member.id,
        mapped_by=user.id,
        mapped_at=now,
    )
    ai_job = AIJobModel(
        id=uuid4(),
        attempt_id=attempt.id,
        practice_session_id=session_id,
        analysis_attempt=1,
        job_type="analyze_session",
        status=AnalysisJobStatus.COMPLETED,
        correlation_id=uuid4(),
        created_at=now,
        updated_at=now,
    )
    session.add_all([stage, speaker_map, ai_job])

    # 4. QA round, question, answer
    qa_round = QARoundModel(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=attempt.id,
        state=QARoundState.COMPLETED,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(qa_round)
    await session.flush()

    question = QuestionModel(
        id=uuid4(),
        qa_round_id=qa_round.id,
        practice_session_id=session_id,
        kind=QuestionKind.PRIMARY,
        position=1,
        text="What is your TAM?",
        reason="Market size clarity",
        rubric_dimension="market",
        evidence_ids=[],
        state=QuestionState.ANSWERED,
        created_at=now,
    )
    session.add(question)
    await session.flush()

    answer = AnswerModel(
        id=uuid4(),
        qa_round_id=qa_round.id,
        question_id=question.id,
        answered_by=user.id,
        status=AnswerStatus.SUBMITTED,
        created_at=now,
        updated_at=now,
    )
    session.add(answer)

    # 5. Evaluation, report, report export
    evaluation = EvaluationModel(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=attempt.id,
        qa_round_id=qa_round.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        overall_score=85.0,
        payload={"score": 85},
        created_at=now,
    )
    session.add(evaluation)
    await session.flush()

    report = ReportModel(
        id=uuid4(),
        practice_session_id=session_id,
        evaluation_id=evaluation.id,
        title="Final Evaluation Report",
        executive_summary="Excellent pitch.",
        overall_score=85.0,
        payload={"summary": "Good"},
        created_at=now,
        updated_at=now,
    )
    session.add(report)
    await session.flush()

    export = ReportExportModel(
        id=uuid4(),
        report_id=report.id,
        practice_session_id=session_id,
        format="pdf",
        status="completed",
        created_at=now,
    )
    idempotency = SessionCommandIdempotencyModel(
        id=uuid4(),
        session_id=session_id,
        actor_id=user.id,
        operation="cancel",
        idempotency_key="cmd-key-1",
        request_hash="hash-1",
        created_at=now,
    )
    session.add_all([export, idempotency])
    await session.flush()

    # Now execute delete via repository
    repo = SqlAlchemyPracticeSessionRepository(session)
    deleted = await repo.delete(session_id)
    assert deleted is True

    # Verify everything related to the session is completely removed from DB
    assert await repo.get_by_id(session_id) is None

    assert not await _scalar_exists(
        session, select(PracticeSessionModel).where(PracticeSessionModel.id == session_id)
    )
    assert not await _scalar_exists(
        session, select(SessionManifestModel).where(SessionManifestModel.session_id == session_id)
    )
    assert not await _scalar_exists(
        session,
        select(SessionManifestDocumentModel).where(
            SessionManifestDocumentModel.manifest_id == manifest.id
        ),
    )
    assert not await _scalar_exists(
        session, select(AnalysisAttemptModel).where(AnalysisAttemptModel.session_id == session_id)
    )
    assert not await _scalar_exists(
        session, select(AnalysisStageModel).where(AnalysisStageModel.attempt_id == attempt.id)
    )
    assert not await _scalar_exists(
        session, select(SpeakerMappingModel).where(SpeakerMappingModel.attempt_id == attempt.id)
    )
    assert not await _scalar_exists(
        session, select(AIJobModel).where(AIJobModel.practice_session_id == session_id)
    )
    assert not await _scalar_exists(
        session, select(QARoundModel).where(QARoundModel.practice_session_id == session_id)
    )
    assert not await _scalar_exists(
        session, select(QuestionModel).where(QuestionModel.practice_session_id == session_id)
    )
    assert not await _scalar_exists(
        session, select(AnswerModel).where(AnswerModel.qa_round_id == qa_round.id)
    )
    assert not await _scalar_exists(
        session, select(EvaluationModel).where(EvaluationModel.practice_session_id == session_id)
    )
    assert not await _scalar_exists(
        session, select(ReportModel).where(ReportModel.practice_session_id == session_id)
    )
    assert not await _scalar_exists(
        session,
        select(ReportExportModel).where(ReportExportModel.practice_session_id == session_id),
    )
    assert not await _scalar_exists(
        session,
        select(SessionCommandIdempotencyModel).where(
            SessionCommandIdempotencyModel.session_id == session_id
        ),
    )

    # Verify parent project and user were not deleted
    assert await _scalar_exists(session, select(ProjectModel).where(ProjectModel.id == project.id))
    assert await _scalar_exists(session, select(UserModel).where(UserModel.id == user.id))


@pytest.mark.anyio
async def test_project_delete_removes_project_and_all_sessions_and_assets(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    user, team, member, project, asset_version = await _setup_test_hierarchy(session)
    now = datetime.now(UTC)

    # Add practice session for the project
    practice_session = PracticeSessionModel(
        id=uuid4(),
        name="Project Session",
        project_id=project.id,
        created_by=user.id,
        status=SessionStatus.READY,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
    )
    session.add(practice_session)
    await session.flush()

    manifest = SessionManifestModel(
        id=uuid4(),
        session_id=practice_session.id,
        presentation_version_id=asset_version.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        frozen_at=now,
    )
    session.add(manifest)

    # Add asset upload idempotency and erasure request
    upload_idempotency = AssetUploadIdempotencyModel(
        id=uuid4(),
        user_id=user.id,
        project_id=project.id,
        operation="create",
        key="key-upload-1",
        request_hash="hash-upload",
        asset_id=asset_version.asset_id,
        version_id=asset_version.id,
        created_at=now,
    )
    erasure_req = ProjectErasureRequestModel(
        id=uuid4(),
        project_id=project.id,
        requested_by=user.id,
        idempotency_key="key-erase-1",
        created_at=now,
    )
    session.add_all([upload_idempotency, erasure_req])
    await session.flush()

    # Execute project delete via repository
    project_repo = SqlAlchemyProjectRepository(session)
    deleted = await project_repo.delete(project.id)
    assert deleted is True

    # Verify project is completely gone from DB
    assert await project_repo.get_by_id(project.id) is None
    assert not await _scalar_exists(
        session, select(ProjectModel).where(ProjectModel.id == project.id)
    )

    # Verify all sessions of the project are completely gone
    assert not await _scalar_exists(
        session,
        select(PracticeSessionModel).where(PracticeSessionModel.project_id == project.id),
    )
    assert not await _scalar_exists(
        session,
        select(SessionManifestModel).where(SessionManifestModel.session_id == practice_session.id),
    )

    # Verify all assets and versions of the project are completely gone
    assert not await _scalar_exists(
        session, select(AssetModel).where(AssetModel.project_id == project.id)
    )
    assert not await _scalar_exists(
        session,
        select(AssetVersionModel).where(AssetVersionModel.asset_id == asset_version.asset_id),
    )
    assert not await _scalar_exists(
        session,
        select(AssetUploadIdempotencyModel).where(
            AssetUploadIdempotencyModel.project_id == project.id
        ),
    )
    assert not await _scalar_exists(
        session,
        select(ProjectErasureRequestModel).where(
            ProjectErasureRequestModel.project_id == project.id
        ),
    )

    # Verify team and user still exist
    assert await _scalar_exists(session, select(TeamModel).where(TeamModel.id == team.id))
    assert await _scalar_exists(session, select(UserModel).where(UserModel.id == user.id))


@pytest.mark.anyio
async def test_asset_delete_removes_asset_and_clears_references(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    user, team, member, project, asset_version = await _setup_test_hierarchy(session)
    now = datetime.now(UTC)
    asset_id = asset_version.asset_id

    # Create another independent asset for session 2 presentation
    other_asset = AssetModel(
        id=uuid4(),
        project_id=project.id,
        kind="presentation_video",
        state="verified",
        file_name="other.mp4",
        created_by=user.id,
        created_at=now,
    )
    session.add(other_asset)
    await session.flush()
    other_version = AssetVersionModel(
        id=uuid4(),
        asset_id=other_asset.id,
        version_number=1,
        state="verified",
        storage_key=f"projects/{project.id}/assets/{other_asset.id}/v1.mp4",
        file_name="other.mp4",
        declared_media_type="video/mp4",
        declared_size_bytes=1000,
        created_by=user.id,
        created_at=now,
        upload_expires_at=now,
    )
    session.add(other_version)
    other_asset.current_version_id = other_version.id
    await session.flush()

    # Session 1: uses asset_version as its primary presentation
    session1 = PracticeSessionModel(
        id=uuid4(),
        name="Session using asset as presentation",
        project_id=project.id,
        created_by=user.id,
        status=SessionStatus.READY,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
    )
    session.add(session1)
    await session.flush()
    manifest1 = SessionManifestModel(
        id=uuid4(),
        session_id=session1.id,
        presentation_version_id=asset_version.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        frozen_at=now,
    )
    session.add(manifest1)
    await session.flush()

    # Session 2: uses other_version as presentation, but asset_version as document, audio, export
    session2 = PracticeSessionModel(
        id=uuid4(),
        name="Session referencing asset as supporting doc/audio",
        project_id=project.id,
        created_by=user.id,
        status=SessionStatus.READY,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
    )
    session.add(session2)
    await session.flush()

    manifest2 = SessionManifestModel(
        id=uuid4(),
        session_id=session2.id,
        presentation_version_id=other_version.id,
        document_version_id=asset_version.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        frozen_at=now,
    )
    session.add(manifest2)
    await session.flush()

    manifest2_doc = SessionManifestDocumentModel(
        id=uuid4(),
        manifest_id=manifest2.id,
        document_version_id=asset_version.id,
        created_at=now,
    )
    session.add(manifest2_doc)

    attempt2 = AnalysisAttemptModel(
        id=uuid4(),
        session_id=session2.id,
        manifest_id=manifest2.id,
        attempt_number=1,
        status=AnalysisAttemptStatus.COMPLETED,
        created_at=now,
        version=1,
    )
    session.add(attempt2)
    await session.flush()

    qa_round2 = QARoundModel(
        id=uuid4(),
        practice_session_id=session2.id,
        analysis_attempt_id=attempt2.id,
        state=QARoundState.IN_PROGRESS,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(qa_round2)
    await session.flush()

    question2 = QuestionModel(
        id=uuid4(),
        qa_round_id=qa_round2.id,
        practice_session_id=session2.id,
        kind=QuestionKind.PRIMARY,
        position=1,
        text="Test question?",
        reason="Test reason",
        rubric_dimension="clarity",
        evidence_ids=[],
        state=QuestionState.ANSWERED,
        created_at=now,
    )
    session.add(question2)
    await session.flush()

    answer2 = AnswerModel(
        id=uuid4(),
        qa_round_id=qa_round2.id,
        question_id=question2.id,
        answered_by=user.id,
        status=AnswerStatus.SUBMITTED,
        audio_asset_version_id=asset_version.id,
        created_at=now,
        updated_at=now,
    )
    session.add(answer2)

    evaluation2 = EvaluationModel(
        id=uuid4(),
        practice_session_id=session2.id,
        analysis_attempt_id=attempt2.id,
        qa_round_id=qa_round2.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        overall_score=90.0,
        payload={"score": 90},
        created_at=now,
    )
    session.add(evaluation2)
    await session.flush()

    report2 = ReportModel(
        id=uuid4(),
        practice_session_id=session2.id,
        evaluation_id=evaluation2.id,
        title="Asset Test Report",
        executive_summary="Good job",
        overall_score=90.0,
        payload={},
        created_at=now,
        updated_at=now,
    )
    session.add(report2)
    await session.flush()

    export2 = ReportExportModel(
        id=uuid4(),
        report_id=report2.id,
        practice_session_id=session2.id,
        format="pdf",
        status="completed",
        asset_version_id=asset_version.id,
        created_at=now,
    )
    upload_idempotency = AssetUploadIdempotencyModel(
        id=uuid4(),
        user_id=user.id,
        project_id=project.id,
        operation="create",
        key="key-asset-upload-1",
        request_hash="hash-asset-upload",
        asset_id=asset_id,
        version_id=asset_version.id,
        created_at=now,
    )
    session.add_all([export2, upload_idempotency])
    await session.flush()

    # Now execute asset delete
    asset_repo = SqlAlchemyAssetRepository(session)
    deleted = await asset_repo.delete_asset(asset_id)
    assert deleted is True

    # Check asset and its versions and idempotency are gone
    assert not await _scalar_exists(session, select(AssetModel).where(AssetModel.id == asset_id))
    assert not await _scalar_exists(
        session, select(AssetVersionModel).where(AssetVersionModel.asset_id == asset_id)
    )
    assert not await _scalar_exists(
        session,
        select(AssetUploadIdempotencyModel).where(AssetUploadIdempotencyModel.asset_id == asset_id),
    )

    # Session 1 (which used asset as presentation) was completely cascade deleted
    assert not await _scalar_exists(
        session, select(PracticeSessionModel).where(PracticeSessionModel.id == session1.id)
    )

    # Session 2 still exists, but its references to the deleted asset are cleared
    assert await _scalar_exists(
        session, select(PracticeSessionModel).where(PracticeSessionModel.id == session2.id)
    )
    assert not await _scalar_exists(
        session,
        select(SessionManifestDocumentModel).where(
            SessionManifestDocumentModel.manifest_id == manifest2.id
        ),
    )
    reloaded_manifest2 = await session.get(SessionManifestModel, manifest2.id)
    assert reloaded_manifest2 is not None
    assert reloaded_manifest2.document_version_id is None
    assert reloaded_manifest2.presentation_version_id == other_version.id

    reloaded_answer2 = await session.get(AnswerModel, answer2.id)
    assert reloaded_answer2 is not None
    assert reloaded_answer2.audio_asset_version_id is None

    reloaded_export2 = await session.get(ReportExportModel, export2.id)
    assert reloaded_export2 is not None
    assert reloaded_export2.asset_version_id is None

    # Verify project and team still exist
    assert await _scalar_exists(session, select(ProjectModel).where(ProjectModel.id == project.id))
