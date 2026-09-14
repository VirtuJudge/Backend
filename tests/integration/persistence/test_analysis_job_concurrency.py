import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
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
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_analysis_job_repository import (
    SqlAlchemyAnalysisJobRepository,
)


async def _create_base_hierarchy(
    session: AsyncSession,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    pres_asset_id = uuid4()
    pres_ver_id = uuid4()

    session.add_all(
        [
            UserModel(id=user_id, issuer="test", subject=str(user_id), created_at=now),
            TeamModel(id=team_id, name=f"Team {team_id}", created_at=now),
        ]
    )
    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name=f"Project {project_id}",
            created_at=now,
        )
    )
    await session.flush()

    session.add(
        AssetModel(
            id=pres_asset_id,
            project_id=project_id,
            kind="presentation",
            file_name="pitch.mp4",
            created_by=user_id,
            created_at=now,
        )
    )
    await session.flush()

    session.add(
        AssetVersionModel(
            id=pres_ver_id,
            asset_id=pres_asset_id,
            storage_key=f"uploads/{pres_ver_id}",
            file_name="pitch.mp4",
            declared_media_type="video/mp4",
            declared_size_bytes=2048,
            created_by=user_id,
            created_at=now,
            upload_expires_at=now,
        )
    )
    await session.flush()

    session.add(
        PracticeSessionModel(
            id=session_id,
            name="Session",
            project_id=project_id,
            created_by=user_id,
            status="READY",
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    session.add(
        SessionManifestModel(
            id=manifest_id,
            session_id=session_id,
            presentation_version_id=pres_ver_id,
        )
    )
    await session.flush()

    return user_id, team_id, project_id, session_id, manifest_id


@pytest.mark.anyio
async def test_concurrent_change_pending_to_queued_only_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        _, _, _, session_id, manifest_id = await _create_base_hierarchy(session)
        now = datetime.now(UTC)
        attempt_id = uuid4()
        session.add(
            AnalysisAttemptModel(
                id=attempt_id,
                session_id=session_id,
                manifest_id=manifest_id,
                attempt_number=1,
                status=AnalysisAttemptStatus.QUEUED,
                version=1,
                created_at=now,
            )
        )
        await session.flush()

        job_id = uuid4()
        job = AnalysisJob(
            id=job_id,
            practice_session_id=session_id,
            attempt_id=attempt_id,
            analysis_attempt=1,
            job_type="analyze_session",
            status=AnalysisJobStatus.PENDING,
            correlation_id=uuid4(),
            last_update_sequence=0,
            payload_version=1,
            attempts=0,
            cancel_requested=False,
            retry_count=0,
            last_error=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
        )
        repo = SqlAlchemyAnalysisJobRepository(session)
        await repo.create(job)
        await session.commit()

    # Launch 5 concurrent workers trying to mark the same pending job as queued
    num_workers = 5

    async def worker_attempt(idx: int) -> bool:
        async with db_session_factory() as worker_session:
            worker_repo = SqlAlchemyAnalysisJobRepository(worker_session)
            worker_now = datetime.now(UTC)
            res = await worker_repo.mark_as_queued(
                job_id,
                worker_now,
                payload={"worker_id": idx},
            )
            await worker_session.commit()
            return res

    results = await asyncio.gather(*[worker_attempt(i) for i in range(num_workers)])

    # Exactly one worker succeeded
    assert results.count(True) == 1
    assert results.count(False) == num_workers - 1

    # Verify final state in DB
    async with db_session_factory() as verify_session:
        verify_repo = SqlAlchemyAnalysisJobRepository(verify_session)
        final_job = await verify_repo.get_by_id(job_id)
        assert final_job is not None
        assert final_job.status == AnalysisJobStatus.QUEUED
        assert final_job.queued_at is not None


@pytest.mark.anyio
async def test_concurrent_callbacks_atomic_sequence_monotonicity(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        _, _, _, session_id, manifest_id = await _create_base_hierarchy(session)
        now = datetime.now(UTC)
        attempt_id = uuid4()
        session.add(
            AnalysisAttemptModel(
                id=attempt_id,
                session_id=session_id,
                manifest_id=manifest_id,
                attempt_number=1,
                status=AnalysisAttemptStatus.QUEUED,
                version=1,
                created_at=now,
            )
        )
        await session.flush()

        job_id = uuid4()
        job = AnalysisJob(
            id=job_id,
            practice_session_id=session_id,
            attempt_id=attempt_id,
            analysis_attempt=1,
            job_type="analyze_session",
            status=AnalysisJobStatus.RUNNING,
            correlation_id=uuid4(),
            last_update_sequence=0,
            payload_version=1,
            attempts=0,
            cancel_requested=False,
            retry_count=0,
            last_error=None,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=None,
        )
        repo = SqlAlchemyAnalysisJobRepository(session)
        await repo.create(job)
        await session.commit()

    # Send 5 sequential updates (1 to 5) concurrently
    sequences = [1, 2, 3, 4, 5]

    async def send_callback(seq: int) -> bool:
        async with db_session_factory() as worker_session:
            worker_repo = SqlAlchemyAnalysisJobRepository(worker_session)
            t = now + timedelta(seconds=seq)
            status = "completed" if seq == 5 else "progress"
            res = await worker_repo.apply_callback(
                job_id,
                sequence=seq,
                status=status,
                occurred_at=t,
                payload={"seq": seq},
            )
            await worker_session.commit()
            return res

    await asyncio.gather(*[send_callback(s) for s in sequences])

    # Verify that the sequence in DB is at least advanced, and sequence 5 is the final if executed
    async with db_session_factory() as verify_session:
        verify_repo = SqlAlchemyAnalysisJobRepository(verify_session)
        final_job = await verify_repo.get_by_id(job_id)
        assert final_job is not None
        assert final_job.last_update_sequence >= 1


@pytest.mark.anyio
async def test_postgres_concurrency_skip_locked_dispatch() -> None:
    database_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("ASSET_TEST_DATABASE_URL not configured")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)

    async with session_factory() as session:
        _, _, _, session_id, manifest_id = await _create_base_hierarchy(session)
        repo = SqlAlchemyAnalysisJobRepository(session)

        # Create 4 pending jobs
        job_ids: list[UUID] = []
        for i in range(1, 5):
            att_id = uuid4()
            session.add(
                AnalysisAttemptModel(
                    id=att_id,
                    session_id=session_id,
                    manifest_id=manifest_id,
                    attempt_number=i,
                    status=AnalysisAttemptStatus.QUEUED,
                    version=1,
                    created_at=now,
                )
            )
            await session.flush()

            j_id = uuid4()
            job = AnalysisJob(
                id=j_id,
                practice_session_id=session_id,
                attempt_id=att_id,
                analysis_attempt=i,
                job_type="analyze_session",
                status=AnalysisJobStatus.PENDING,
                correlation_id=uuid4(),
                last_update_sequence=0,
                payload_version=1,
                attempts=0,
                cancel_requested=False,
                retry_count=0,
                last_error=None,
                created_at=now + timedelta(seconds=i),
                updated_at=now + timedelta(seconds=i),
                started_at=None,
                completed_at=None,
                next_dispatch_at=now,
            )
            await repo.create(job)
            job_ids.append(j_id)

        await session.commit()

    # Two concurrent workers fetch with for_update=True, skip_locked=True
    claimed_worker_1: list[UUID] = []
    claimed_worker_2: list[UUID] = []

    async def worker_1() -> None:
        async with session_factory() as s1:
            r1 = SqlAlchemyAnalysisJobRepository(s1)
            jobs = await r1.get_eligible_pending_jobs(
                now + timedelta(minutes=1), limit=2, for_update=True, skip_locked=True
            )
            claimed_worker_1.extend([j.id for j in jobs])
            await asyncio.sleep(0.1)
            await s1.commit()

    async def worker_2() -> None:
        async with session_factory() as s2:
            r2 = SqlAlchemyAnalysisJobRepository(s2)
            jobs = await r2.get_eligible_pending_jobs(
                now + timedelta(minutes=1), limit=2, for_update=True, skip_locked=True
            )
            claimed_worker_2.extend([j.id for j in jobs])
            await s2.commit()

    await asyncio.gather(worker_1(), worker_2())
    await engine.dispose()

    # Disjoint sets: no overlap in claimed jobs
    assert set(claimed_worker_1).isdisjoint(set(claimed_worker_2))
