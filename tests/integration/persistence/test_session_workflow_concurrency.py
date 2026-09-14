import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AnalyzeSessionPayload,
)
from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
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
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow import SqlAlchemyUnitOfWork

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.anyio
async def test_simultaneous_analysis_start_is_idempotent_in_postgres() -> None:
    database_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("ASSET_TEST_DATABASE_URL not configured")

    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(configuration, "head")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    asset_version_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    idempotency_key = f"start-{uuid4()}"

    try:
        async with session_factory() as setup_session:
            setup_session.add_all(
                [
                    UserModel(
                        id=user_id,
                        email=f"workflow-race-{uuid4().hex[:8]}@example.com",
                        issuer="https://auth.example",
                        subject=f"workflow-race-{uuid4()}",
                        created_at=now,
                    ),
                    TeamModel(id=team_id, name=f"Race team {uuid4()}", created_at=now),
                ]
            )
            await setup_session.flush()
            setup_session.add_all(
                [
                    TeamMemberModel(
                        id=uuid4(),
                        team_id=team_id,
                        user_id=user_id,
                        role="owner",
                        joined_at=now,
                    ),
                    ProjectModel(
                        id=project_id,
                        team_id=team_id,
                        name="Analysis start race",
                        description=None,
                        created_at=now,
                    ),
                ]
            )
            await setup_session.flush()
            setup_session.add(
                AssetModel(
                    id=asset_id,
                    project_id=project_id,
                    kind="presentation_video",
                    state="verified",
                    file_name="presentation.mp4",
                    current_version_id=asset_version_id,
                    created_by=user_id,
                    created_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                AssetVersionModel(
                    id=asset_version_id,
                    asset_id=asset_id,
                    version_number=1,
                    state="verified",
                    storage_key=f"tests/{asset_version_id}",
                    file_name="presentation.mp4",
                    declared_media_type="video/mp4",
                    declared_size_bytes=1,
                    media_type="video/mp4",
                    size_bytes=1,
                    checksum=f"sha256:{'0' * 64}",
                    created_by=user_id,
                    created_at=now,
                    completed_at=now,
                    upload_expires_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                PracticeSessionModel(
                    id=session_id,
                    name="Concurrent analysis start",
                    project_id=project_id,
                    created_by=user_id,
                    status=SessionStatus.READY,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    consent_granted=False,
                )
            )
            await setup_session.flush()
            setup_session.add(
                SessionManifestModel(
                    id=manifest_id,
                    session_id=session_id,
                    presentation_version_id=asset_version_id,
                    rubric_id="startup_pitch",
                    rubric_version=1,
                )
            )
            await setup_session.commit()

        async def start_analysis() -> AnalysisAttempt:
            async with session_factory() as database_session:
                workflow = SessionWorkflow(SqlAlchemyUnitOfWork(database_session))
                return await workflow.create_analysis_attempt(
                    session_id=session_id,
                    actor_id=user_id,
                    idempotency_key=idempotency_key,
                    consent_accepted=True,
                    consent_policy_version=1,
                )

        first, second = await asyncio.gather(start_analysis(), start_analysis())
        assert first.id == second.id

        repeated = await start_analysis()
        assert repeated.id == first.id

        async with session_factory() as verification_session:
            attempt_count = await verification_session.scalar(
                select(func.count())
                .select_from(AnalysisAttemptModel)
                .where(AnalysisAttemptModel.session_id == session_id)
            )
            job = await verification_session.scalar(
                select(AnalysisJobModel).where(AnalysisJobModel.practice_session_id == session_id)
            )
        assert attempt_count == 1
        assert job is not None
        assert job.attempt_id == first.id
        assert job.analysis_attempt == 1
        assert job.job_type == "analyze_session"
        assert job.status == AnalysisJobStatus.PENDING
        assert job.payload is not None

        schema_path = ROOT / "contracts" / "schemas" / "ai_job.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(job.payload)

        parsed = AIJobQueueMessage.model_validate(job.payload)
        assert parsed.schema_version == 1
        assert parsed.job_type == AIJobType.ANALYZE_SESSION
        assert parsed.practice_session_id == str(session_id)
        assert parsed.analysis_attempt == 1
        assert parsed.trace_id.startswith("trc_")
        assert isinstance(parsed.payload, AnalyzeSessionPayload)
        assert parsed.payload.presentation.artifact_id == str(asset_version_id)
        assert parsed.payload.presentation.object_key == f"tests/{asset_version_id}"
        assert parsed.payload.presentation.checksum == f"sha256:{'0' * 64}"
        assert parsed.payload.presentation.media_type == "video/mp4"
        assert parsed.payload.rubric.rubric_id == "startup_pitch"
        assert parsed.payload.rubric.version == 1
        assert parsed.payload.requested_capabilities == [
            "speech",
            "diarization",
            "vision",
            "audio",
            "documents",
            "questions",
        ]
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_simultaneous_retry_is_idempotent_in_postgres() -> None:
    database_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("ASSET_TEST_DATABASE_URL not configured")

    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(configuration, "head")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    asset_version_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    failed_attempt_id = uuid4()
    retry_key = f"retry-{uuid4()}"

    try:
        async with session_factory() as setup_session:
            setup_session.add_all(
                [
                    UserModel(
                        id=user_id,
                        email=f"retry-race-{uuid4().hex[:8]}@example.com",
                        issuer="https://auth.example",
                        subject=f"retry-race-{uuid4()}",
                        created_at=now,
                    ),
                    TeamModel(id=team_id, name=f"Retry team {uuid4()}", created_at=now),
                ]
            )
            await setup_session.flush()
            setup_session.add_all(
                [
                    TeamMemberModel(
                        id=uuid4(),
                        team_id=team_id,
                        user_id=user_id,
                        role="owner",
                        joined_at=now,
                    ),
                    ProjectModel(
                        id=project_id,
                        team_id=team_id,
                        name="Analysis retry race",
                        description=None,
                        created_at=now,
                    ),
                ]
            )
            await setup_session.flush()
            setup_session.add(
                AssetModel(
                    id=asset_id,
                    project_id=project_id,
                    kind="presentation_video",
                    state="verified",
                    file_name="presentation.mp4",
                    current_version_id=asset_version_id,
                    created_by=user_id,
                    created_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                AssetVersionModel(
                    id=asset_version_id,
                    asset_id=asset_id,
                    version_number=1,
                    state="verified",
                    storage_key=f"tests/{asset_version_id}",
                    file_name="presentation.mp4",
                    declared_media_type="video/mp4",
                    declared_size_bytes=1,
                    media_type="video/mp4",
                    size_bytes=1,
                    checksum=f"sha256:{'0' * 64}",
                    created_by=user_id,
                    created_at=now,
                    completed_at=now,
                    upload_expires_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                PracticeSessionModel(
                    id=session_id,
                    name="Concurrent retry",
                    project_id=project_id,
                    created_by=user_id,
                    status=SessionStatus.FAILED,
                    version=2,
                    created_at=now,
                    updated_at=now,
                    consent_granted=True,
                )
            )
            await setup_session.flush()
            setup_session.add(
                SessionManifestModel(
                    id=manifest_id,
                    session_id=session_id,
                    presentation_version_id=asset_version_id,
                    rubric_id="startup_pitch",
                    rubric_version=1,
                    snapshot={
                        "schema_version": 1,
                        "presentation": {
                            "artifact_id": str(asset_version_id),
                            "object_key": f"tests/{asset_version_id}",
                            "checksum": f"sha256:{'0' * 64}",
                            "media_type": "video/mp4",
                        },
                        "supporting_documents": [],
                        "rubric": {"rubric_id": "startup_pitch", "version": 1},
                    },
                    frozen_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                AnalysisAttemptModel(
                    id=failed_attempt_id,
                    session_id=session_id,
                    manifest_id=manifest_id,
                    attempt_number=1,
                    status=AnalysisAttemptStatus.FAILED,
                    idempotency_key="initial-start",
                    version=1,
                    created_at=now,
                    failed_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                AnalysisJobModel(
                    id=uuid4(),
                    practice_session_id=session_id,
                    attempt_id=failed_attempt_id,
                    analysis_attempt=1,
                    job_type="analyze_session",
                    status=AnalysisJobStatus.FAILED,
                    correlation_id=uuid4(),
                    last_update_sequence=1,
                    payload_version=1,
                    attempts=1,
                    cancel_requested=False,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                    payload={"schema_version": 1, "job_type": "analyze_session"},
                )
            )
            await setup_session.commit()

        async def run_retry() -> AnalysisAttempt:
            async with session_factory() as database_session:
                workflow = SessionWorkflow(SqlAlchemyUnitOfWork(database_session))
                return await workflow.retry(
                    session_id=session_id,
                    actor_id=user_id,
                    idempotency_key=retry_key,
                )

        first_retry, second_retry = await asyncio.gather(run_retry(), run_retry())
        assert first_retry.id == second_retry.id
        assert first_retry.attempt_number == 2

        repeated_retry = await run_retry()
        assert repeated_retry.id == first_retry.id

        async with session_factory() as verification_session:
            attempt_count = await verification_session.scalar(
                select(func.count())
                .select_from(AnalysisAttemptModel)
                .where(AnalysisAttemptModel.session_id == session_id)
            )
            job_count = await verification_session.scalar(
                select(func.count())
                .select_from(AnalysisJobModel)
                .where(AnalysisJobModel.practice_session_id == session_id)
            )
            retried_job = await verification_session.scalar(
                select(AnalysisJobModel).where(
                    AnalysisJobModel.practice_session_id == session_id,
                    AnalysisJobModel.attempt_id == first_retry.id,
                )
            )

        assert attempt_count == 2
        assert job_count == 2
        assert retried_job is not None
        assert retried_job.analysis_attempt == 2
        assert retried_job.status == AnalysisJobStatus.PENDING
        assert retried_job.payload is not None

        schema_path = ROOT / "contracts" / "schemas" / "ai_job.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(retried_job.payload)

        parsed = AIJobQueueMessage.model_validate(retried_job.payload)
        assert parsed.schema_version == 1
        assert parsed.analysis_attempt == 2
        assert parsed.job_type == AIJobType.ANALYZE_SESSION
        assert parsed.practice_session_id == str(session_id)
    finally:
        await engine.dispose()
