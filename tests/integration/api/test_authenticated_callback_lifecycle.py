from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    ArtifactRef,
    PrimaryQuestion,
    ProgressPayload,
    SessionAnalysisCompletedPayload,
    StartedPayload,
)
from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.project_configuration import ProjectModel
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.persistence.configurations.team_configuration import TeamModel
from app.infrastructure.persistence.configurations.team_member_configuration import (
    TeamMemberModel,
)
from app.infrastructure.persistence.configurations.user_configuration import UserModel
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from app.main import create_app
from app.settings import Settings

SHARED_WORKER_SECRET = "test-worker-auth-secret-key-12345"


@pytest.fixture
async def test_env(
    tmp_path: Path,
) -> tuple[async_sessionmaker[AsyncSession], AsyncClient, Settings]:
    db_path = tmp_path / "auth_callback_lifecycle.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    settings = Settings(
        app_env="test",
        database_url=db_url,
        ai_worker_shared_secret=SecretStr(SHARED_WORKER_SECRET),
        ai_job_dispatcher_enabled=False,
    )

    app = create_app(settings)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    return session_maker, client, settings


async def _seed_session_and_job(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    session_status: SessionStatus = SessionStatus.ANALYZING,
    job_status: AnalysisJobStatus = AnalysisJobStatus.QUEUED,
    attempt_status: AnalysisAttemptStatus = AnalysisAttemptStatus.RUNNING,
    analysis_attempt_num: int = 1,
    trace_id: str = "trc_auth_lifecycle_1",
    cancel_requested: bool = False,
) -> tuple[UUID, UUID, UUID, UUID]:
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    attempt_id = uuid4()
    job_id = uuid4()
    manifest_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()

    async with session_maker() as s:
        s.add_all(
            [
                UserModel(
                    id=user_id,
                    email="worker_test@example.com",
                    issuer="test",
                    subject=f"sub-{user_id}",
                    created_at=now,
                ),
                TeamModel(id=team_id, name="AI Team", created_at=now, version=1),
            ]
        )
        await s.flush()

        s.add_all(
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
                    name="AI Project",
                    description="Desc",
                    created_at=now,
                    version=1,
                ),
            ]
        )
        await s.flush()

        s.add(
            AssetModel(
                id=asset_id,
                project_id=project_id,
                kind="presentation_video",
                file_name="video.mp4",
                created_by=user_id,
                created_at=now,
            )
        )
        await s.flush()

        s.add(
            AssetVersionModel(
                id=version_id,
                asset_id=asset_id,
                storage_key=f"uploads/{team_id}/{project_id}/video.mp4",
                file_name="video.mp4",
                declared_media_type="video/mp4",
                declared_size_bytes=1024,
                state="verified",
                checksum=f"sha256:{'1' * 64}",
                created_by=user_id,
                created_at=now,
                upload_expires_at=now + timedelta(hours=1),
            )
        )
        await s.flush()

        s.add(
            PracticeSessionModel(
                id=session_id,
                name="AI Practice Session",
                project_id=project_id,
                created_by=user_id,
                status=session_status,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        await s.flush()

        s.add(
            SessionManifestModel(
                id=manifest_id,
                session_id=session_id,
                presentation_version_id=version_id,
                rubric_id="startup_pitch",
                rubric_version=1,
                frozen_at=now,
            )
        )
        await s.flush()

        s.add(
            AnalysisAttemptModel(
                id=attempt_id,
                session_id=session_id,
                manifest_id=manifest_id,
                attempt_number=analysis_attempt_num,
                status=attempt_status,
                version=1,
                created_at=now,
                started_at=now,
            )
        )
        await s.flush()

        s.add(
            AnalysisJobModel(
                id=job_id,
                practice_session_id=session_id,
                attempt_id=attempt_id,
                analysis_attempt=analysis_attempt_num,
                job_type="analyze_session",
                status=job_status,
                correlation_id=uuid4(),
                last_update_sequence=0,
                payload_version=1,
                attempts=1,
                cancel_requested=cancel_requested,
                retry_count=0,
                created_at=now,
                updated_at=now,
                started_at=now,
                payload={"trace_id": trace_id},
            )
        )
        await s.commit()

    return session_id, attempt_id, job_id, user_id


@pytest.mark.anyio
async def test_authenticated_callback_complete_lifecycle(
    test_env: tuple[async_sessionmaker[AsyncSession], AsyncClient, Settings],
) -> None:
    session_maker, client, _ = test_env
    trace_id = "trc_auth_lifecycle_success"
    session_id, attempt_id, job_id, user_id = await _seed_session_and_job(
        session_maker,
        job_status=AnalysisJobStatus.QUEUED,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        analysis_attempt_num=1,
        trace_id=trace_id,
    )

    auth_header = {"Authorization": f"Bearer {SHARED_WORKER_SECRET}"}
    now = datetime.now(UTC)

    # -----------------------------------------------------------------------
    # Step 5: Worker authentication protects status and update endpoints
    # -----------------------------------------------------------------------
    res_status_no_auth = await client.get(f"/internal/v1/ai-jobs/{job_id}")
    assert res_status_no_auth.status_code == 401

    res_status_wrong_auth = await client.get(
        f"/internal/v1/ai-jobs/{job_id}",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert res_status_wrong_auth.status_code == 401

    res_status_valid = await client.get(f"/internal/v1/ai-jobs/{job_id}", headers=auth_header)
    assert res_status_valid.status_code == 200
    status_data = res_status_valid.json()
    assert status_data["id"] == str(job_id)
    assert status_data["job_id"] == str(job_id)
    assert status_data["status"] == "queued"
    assert status_data["last_update_sequence"] == 0
    assert status_data["cancel_requested"] is False

    started_update = AIWorkerUpdate(
        schema_version=1,
        sequence=1,
        status=AIWorkerUpdateStatus.STARTED,
        occurred_at=now,
        trace_id=trace_id,
        payload=StartedPayload(pipeline_version="1.0.0"),
    ).model_dump(mode="json")

    res_update_no_auth = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        json=started_update,
    )
    assert res_update_no_auth.status_code == 401

    res_update_wrong_auth = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers={"Authorization": "Bearer bad-token"},
        json=started_update,
    )
    assert res_update_wrong_auth.status_code == 401

    # -----------------------------------------------------------------------
    # Step 6: Started and progress callbacks advance monotonically
    # -----------------------------------------------------------------------
    # 6a. STARTED update
    res_started = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=started_update,
    )
    assert res_started.status_code == 200
    started_data = res_started.json()
    assert started_data["status"] == "running"
    assert started_data["last_update_sequence"] == 1

    # 6b. PROGRESS sequence 2
    progress_update_2 = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.PROGRESS,
        occurred_at=now + timedelta(seconds=1),
        trace_id=trace_id,
        payload=ProgressPayload(
            stage="transcription",
            progress=0.25,
            message="Transcribing speech",
        ),
    ).model_dump(mode="json")

    res_prog_2 = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=progress_update_2,
    )
    assert res_prog_2.status_code == 200
    prog_2_data = res_prog_2.json()
    assert prog_2_data["status"] == "running"
    assert prog_2_data["last_update_sequence"] == 2

    # 6c. PROGRESS sequence 3
    progress_update_3 = AIWorkerUpdate(
        schema_version=1,
        sequence=3,
        status=AIWorkerUpdateStatus.PROGRESS,
        occurred_at=now + timedelta(seconds=2),
        trace_id=trace_id,
        payload=ProgressPayload(stage="analysis", progress=0.60, message="Analyzing questions"),
    ).model_dump(mode="json")

    res_prog_3 = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=progress_update_3,
    )
    assert res_prog_3.status_code == 200
    prog_3_data = res_prog_3.json()
    assert prog_3_data["status"] == "running"
    assert prog_3_data["last_update_sequence"] == 3

    # -----------------------------------------------------------------------
    # Step 7: Duplicate callbacks have no repeated side effects
    # -----------------------------------------------------------------------
    # Replay sequence 2 (older duplicate)
    res_dup_2 = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=progress_update_2,
    )
    assert res_dup_2.status_code == 200
    assert res_dup_2.json()["last_update_sequence"] == 3
    assert res_dup_2.json()["status"] == "running"

    # Replay sequence 3 (same sequence duplicate)
    res_dup_3 = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=progress_update_3,
    )
    assert res_dup_3.status_code == 200
    assert res_dup_3.json()["last_update_sequence"] == 3
    assert res_dup_3.json()["status"] == "running"

    # -----------------------------------------------------------------------
    # Step 8: Valid completion finishes AI Job and Analysis Attempt
    # -----------------------------------------------------------------------
    completed_update = AIWorkerUpdate(
        schema_version=1,
        sequence=4,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=now + timedelta(seconds=3),
        trace_id=trace_id,
        payload=SessionAnalysisCompletedPayload(
            analysis_artifact=ArtifactRef(
                artifact_id="01JEXAMPLE0000000000000099",
                object_key="artifacts/session_analysis.json",
                checksum=f"sha256:{'2' * 64}",
                schema_version=1,
            ),
            primary_questions=[
                PrimaryQuestion(
                    candidate_id="c1",
                    text="What is your customer churn rate?",
                    reason="Assesses retention economics",
                    rubric_dimension="traction",
                    evidence_ids=["ev1"],
                ),
                PrimaryQuestion(
                    candidate_id="c2",
                    text="How do you protect your IP from competitors?",
                    reason="Assesses competitive defensibility",
                    rubric_dimension="competition",
                    evidence_ids=["ev2"],
                ),
                PrimaryQuestion(
                    candidate_id="c3",
                    text="What is your runway based on current burn?",
                    reason="Assesses financial sustainability",
                    rubric_dimension="financials",
                    evidence_ids=["ev3"],
                ),
            ],
            speaker_labels=["SPEAKER_00"],
            limitations=[],
        ),
    ).model_dump(mode="json")

    res_completed = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers=auth_header,
        json=completed_update,
    )
    assert res_completed.status_code == 200
    completed_data = res_completed.json()
    assert completed_data["status"] == "completed"
    assert completed_data["last_update_sequence"] == 4

    # Verify DB persistence of completion
    async with session_maker() as verify_session:
        db_job = await verify_session.get(AnalysisJobModel, job_id)
        assert db_job is not None
        assert db_job.status == AnalysisJobStatus.COMPLETED
        assert db_job.completed_at is not None
        assert db_job.last_update_sequence == 4
        assert db_job.completed_result is not None

        db_attempt = await verify_session.get(AnalysisAttemptModel, attempt_id)
        assert db_attempt is not None
        assert db_attempt.status == AnalysisAttemptStatus.RUNNING
        assert db_attempt.completed_at is None


@pytest.mark.anyio
async def test_authenticated_callback_cancelled_and_superseded_ignored(
    test_env: tuple[async_sessionmaker[AsyncSession], AsyncClient, Settings],
) -> None:
    session_maker, client, _ = test_env
    auth_header = {"Authorization": f"Bearer {SHARED_WORKER_SECRET}"}
    now = datetime.now(UTC)

    # -----------------------------------------------------------------------
    # Step 9A: Cancelled session/job ignores late progress and completion
    # -----------------------------------------------------------------------
    trace_cancelled = "trc_cancelled_session"
    c_session_id, c_attempt_id, c_job_id, c_user_id = await _seed_session_and_job(
        session_maker,
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        analysis_attempt_num=1,
        trace_id=trace_cancelled,
    )

    # Cancel session via SessionWorkflow
    async with session_maker() as s:
        workflow = SessionWorkflow(SqlAlchemyUnitOfWork(s))
        await workflow.cancel(session_id=c_session_id, actor_id=c_user_id, reason="User cancelled")

    # Send progress to cancelled job
    late_progress = AIWorkerUpdate(
        schema_version=1,
        sequence=1,
        status=AIWorkerUpdateStatus.PROGRESS,
        occurred_at=now,
        trace_id=trace_cancelled,
        payload=ProgressPayload(stage="speech", progress=0.5, message="Processing"),
    ).model_dump(mode="json")

    res_late_prog = await client.post(
        f"/internal/v1/ai-jobs/{c_job_id}/updates",
        headers=auth_header,
        json=late_progress,
    )
    assert res_late_prog.status_code == 200
    late_prog_data = res_late_prog.json()
    assert late_prog_data["status"] == "cancelled"
    assert late_prog_data["cancel_requested"] is True
    assert late_prog_data["last_update_sequence"] == 1

    # Send completion to cancelled job
    late_completed = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=now + timedelta(seconds=1),
        trace_id=trace_cancelled,
        payload=SessionAnalysisCompletedPayload(
            analysis_artifact=ArtifactRef(
                artifact_id="01JEXAMPLE0000000000000099",
                object_key="artifacts/session_analysis.json",
                checksum=f"sha256:{'3' * 64}",
                schema_version=1,
            ),
            primary_questions=[
                PrimaryQuestion(
                    candidate_id="c1",
                    text="Q1?",
                    reason="R1",
                    rubric_dimension="dim1",
                    evidence_ids=["ev1"],
                ),
                PrimaryQuestion(
                    candidate_id="c2",
                    text="Q2?",
                    reason="R2",
                    rubric_dimension="dim2",
                    evidence_ids=["ev2"],
                ),
                PrimaryQuestion(
                    candidate_id="c3",
                    text="Q3?",
                    reason="R3",
                    rubric_dimension="dim3",
                    evidence_ids=["ev3"],
                ),
            ],
            speaker_labels=["SPEAKER_00"],
            limitations=[],
        ),
    ).model_dump(mode="json")

    res_late_comp = await client.post(
        f"/internal/v1/ai-jobs/{c_job_id}/updates",
        headers=auth_header,
        json=late_completed,
    )
    assert res_late_comp.status_code == 200
    late_comp_data = res_late_comp.json()
    assert late_comp_data["status"] == "cancelled"
    assert late_comp_data["cancel_requested"] is True
    assert late_comp_data["last_update_sequence"] == 2

    # Verify attempt and job in DB remain cancelled and completed_result was NOT persisted
    async with session_maker() as verify_session:
        db_job = await verify_session.get(AnalysisJobModel, c_job_id)
        assert db_job is not None
        assert db_job.status == AnalysisJobStatus.CANCELLED
        assert db_job.completed_result is None

        db_attempt = await verify_session.get(AnalysisAttemptModel, c_attempt_id)
        assert db_attempt is not None
        assert db_attempt.status == AnalysisAttemptStatus.CANCELLED

    # -----------------------------------------------------------------------
    # Step 9B: Superseded older attempt ignores completed results
    # -----------------------------------------------------------------------
    trace_superseded = "trc_superseded_old"
    s_session_id, s_attempt_1_id, s_job_1_id, s_user_id = await _seed_session_and_job(
        session_maker,
        session_status=SessionStatus.FAILED,
        job_status=AnalysisJobStatus.FAILED,
        attempt_status=AnalysisAttemptStatus.FAILED,
        analysis_attempt_num=1,
        trace_id=trace_superseded,
    )

    # Retry session to create attempt 2
    async with session_maker() as s:
        workflow = SessionWorkflow(SqlAlchemyUnitOfWork(s))
        attempt_2 = await workflow.retry(
            session_id=s_session_id,
            actor_id=s_user_id,
            idempotency_key="idemp-retry-for-stale-test",
        )

    assert attempt_2.attempt_number == 2

    # Send completion update to older job (job 1)
    stale_completion = AIWorkerUpdate(
        schema_version=1,
        sequence=5,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=now + timedelta(seconds=5),
        trace_id=trace_superseded,
        payload=SessionAnalysisCompletedPayload(
            analysis_artifact=ArtifactRef(
                artifact_id="01JEXAMPLE0000000000000099",
                object_key="artifacts/session_analysis.json",
                checksum=f"sha256:{'4' * 64}",
                schema_version=1,
            ),
            primary_questions=[
                PrimaryQuestion(
                    candidate_id="c1",
                    text="Q1?",
                    reason="R1",
                    rubric_dimension="dim1",
                    evidence_ids=["ev1"],
                ),
                PrimaryQuestion(
                    candidate_id="c2",
                    text="Q2?",
                    reason="R2",
                    rubric_dimension="dim2",
                    evidence_ids=["ev2"],
                ),
                PrimaryQuestion(
                    candidate_id="c3",
                    text="Q3?",
                    reason="R3",
                    rubric_dimension="dim3",
                    evidence_ids=["ev3"],
                ),
            ],
            speaker_labels=["SPEAKER_00"],
            limitations=[],
        ),
    ).model_dump(mode="json")

    res_stale = await client.post(
        f"/internal/v1/ai-jobs/{s_job_1_id}/updates",
        headers=auth_header,
        json=stale_completion,
    )
    assert res_stale.status_code == 200
    assert res_stale.json()["last_update_sequence"] == 5

    # Verify current session and current attempt 2 are completely unaffected
    async with session_maker() as verify_session:
        db_session = await verify_session.get(PracticeSessionModel, s_session_id)
        assert db_session is not None
        assert db_session.status == SessionStatus.ANALYZING

        db_attempt_2 = await verify_session.get(AnalysisAttemptModel, attempt_2.id)
        assert db_attempt_2 is not None
        assert db_attempt_2.status == AnalysisAttemptStatus.QUEUED
        assert db_attempt_2.completed_at is None
