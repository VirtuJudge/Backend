from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.ai_jobs import AIJobs
from app.application.ports.ai_queue import AIQueueTemporaryFailure
from app.application.session_workflow import (
    CURRENT_CONSENT_POLICY_VERSION,
    SessionWorkflow,
)
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
from tests.support.fake_ai_job_queue import FakeAIJobQueue


@pytest.mark.anyio
async def test_commit_to_enqueue_recovery_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(UTC)
        user_id = uuid4()
        team_id = uuid4()
        project_id = uuid4()
        asset_id = uuid4()
        version_id = uuid4()
        session_id = uuid4()

        async with session_maker() as setup_session:
            setup_session.add_all(
                [
                    UserModel(
                        id=user_id,
                        email="presenter@example.com",
                        issuer="test",
                        subject="sub-presenter-1",
                        created_at=now,
                    ),
                    TeamModel(
                        id=team_id,
                        name="Pitch Team",
                        created_at=now,
                        version=1,
                    ),
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
                        name="Pitch Project",
                        description="Pitch Project Description",
                        created_at=now,
                        version=1,
                    ),
                ]
            )
            await setup_session.flush()

            setup_session.add(
                AssetModel(
                    id=asset_id,
                    project_id=project_id,
                    kind="presentation_video",
                    file_name="presentation.mp4",
                    created_by=user_id,
                    created_at=now,
                )
            )
            await setup_session.flush()

            setup_session.add(
                AssetVersionModel(
                    id=version_id,
                    asset_id=asset_id,
                    storage_key=f"uploads/{team_id}/{project_id}/presentation.mp4",
                    file_name="presentation.mp4",
                    declared_media_type="video/mp4",
                    declared_size_bytes=4096,
                    state="verified",
                    checksum=f"sha256:{'a' * 64}",
                    created_by=user_id,
                    created_at=now,
                    upload_expires_at=now + timedelta(hours=1),
                )
            )
            await setup_session.flush()

            setup_session.add(
                PracticeSessionModel(
                    id=session_id,
                    name="Quarterly Pitch Session",
                    project_id=project_id,
                    created_by=user_id,
                    status=SessionStatus.READY,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await setup_session.flush()

            manifest_id = uuid4()
            setup_session.add(
                SessionManifestModel(
                    id=manifest_id,
                    session_id=session_id,
                    presentation_version_id=version_id,
                    rubric_id="startup_pitch",
                    rubric_version=1,
                )
            )
            await setup_session.commit()

        # Step 2 & 3: Immediate publish fails with simulated broker failure
        queue = FakeAIJobQueue()
        queue.fail_next(1, AIQueueTemporaryFailure("Simulated broker unreachable"))

        async with session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            workflow = SessionWorkflow(uow, queue=queue)

            # Step 1: analysis start commits one durable pending AI Job
            attempt = await workflow.create_analysis_attempt(
                session_id=session_id,
                actor_id=user_id,
                idempotency_key="idemp-recovery-flow-key",
                consent_accepted=True,
                consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
            )

        assert attempt is not None
        assert attempt.attempt_number == 1
        assert attempt.status == AnalysisAttemptStatus.QUEUED

        # Verify DB state after failed immediate publish: durable pending AI job
        async with session_maker() as verify_session:
            db_session = await verify_session.get(PracticeSessionModel, session_id)
            assert db_session is not None
            assert db_session.status == SessionStatus.ANALYZING

            db_attempt = await verify_session.get(AnalysisAttemptModel, attempt.id)
            assert db_attempt is not None
            assert db_attempt.status == AnalysisAttemptStatus.QUEUED

            job_stmt = select(AnalysisJobModel).where(
                AnalysisJobModel.practice_session_id == session_id
            )
            db_job = await verify_session.scalar(job_stmt)
            assert db_job is not None
            assert db_job.status == AnalysisJobStatus.PENDING
            assert db_job.attempts == 0
            assert db_job.queued_at is None
            assert db_job.dispatch_retry_count == 1
            assert db_job.last_dispatch_error_category == "temporary_queue_failure"

            stable_job_id = db_job.id

        # Step 4: redispatch later publishes the same stable job ID
        assert queue.count == 0  # failed first publish did not append

        async with session_maker() as redispatch_session:
            uow = SqlAlchemyUnitOfWork(redispatch_session)
            ai_jobs = AIJobs(uow, queue=queue)
            result = await ai_jobs.redispatch_pending(
                now=now + timedelta(minutes=2),
                limit=10,
            )

        assert result.processed == 1
        assert result.queued == 1
        assert result.failed == 0

        # Verify the queue published message has the same stable job ID
        assert queue.count == 1
        published_msg = queue.published_messages[0]
        assert str(published_msg.job_id) == str(stable_job_id)
        assert str(published_msg.practice_session_id) == str(session_id)
        assert published_msg.analysis_attempt == 1

        # Verify AI Job in database is now QUEUED
        async with session_maker() as verify_session:
            db_job_queued = await verify_session.get(AnalysisJobModel, stable_job_id)
            assert db_job_queued is not None
            assert db_job_queued.status == AnalysisJobStatus.QUEUED
            assert db_job_queued.attempts == 0
            assert db_job_queued.queued_at is not None
    finally:
        await engine.dispose()
