from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
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
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_analysis_job_repository import (
    SqlAlchemyAnalysisJobRepository,
)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def _create_test_context_entities(
    session: AsyncSession,
) -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
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
            kind="presentation",
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

    session.add(
        PracticeSessionModel(
            id=session_id,
            name="Test Practice Session",
            project_id=project_id,
            created_by=user_id,
            status=SessionStatus.READY,
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
            presentation_version_id=presentation_version_id,
            rubric_id="startup_pitch",
            rubric_version=1,
            snapshot={"version": 1},
            frozen_at=now,
        )
    )
    await session.flush()

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

    return user_id, team_id, project_id, session_id, manifest_id, attempt_id


def _make_job(
    session_id: UUID,
    attempt_id: UUID,
    attempt_number: int = 1,
    status: AnalysisJobStatus = AnalysisJobStatus.PENDING,
    now: datetime | None = None,
    job_id: UUID | None = None,
    correlation_id: UUID | None = None,
    next_dispatch_at: datetime | None = None,
    cancel_requested: bool = False,
) -> AnalysisJob:
    ts = now or datetime.now(UTC)
    return AnalysisJob(
        id=job_id or uuid4(),
        practice_session_id=session_id,
        attempt_id=attempt_id,
        analysis_attempt=attempt_number,
        job_type="analyze_session",
        status=status,
        correlation_id=correlation_id or uuid4(),
        last_update_sequence=0,
        payload_version=1,
        attempts=0,
        cancel_requested=cancel_requested,
        retry_count=0,
        last_error=None,
        created_at=ts,
        updated_at=ts,
        started_at=None,
        completed_at=None,
        payload={"job_type": "analyze_session", "session_id": str(session_id)},
        queued_at=None,
        next_dispatch_at=next_dispatch_at,
        dispatch_retry_count=0,
        last_dispatch_error_category=None,
        completed_result=None,
    )


@pytest.mark.anyio
async def test_create_and_get_by_id_and_attempt_id(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    job = _make_job(session_id, attempt_id)
    created = await repo.create(job)
    assert created.id == job.id

    fetched = await repo.get_by_id(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.attempt_id == attempt_id
    assert fetched.practice_session_id == session_id
    assert fetched.status == AnalysisJobStatus.PENDING
    assert fetched.payload == job.payload

    by_attempt = await repo.get_by_attempt_id(attempt_id)
    assert by_attempt is not None
    assert by_attempt.id == job.id

    non_existent = await repo.get_by_id(uuid4())
    assert non_existent is None


@pytest.mark.anyio
async def test_update_persists_every_intended_mutable_field(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    created_at = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, now=created_at)
    await repo.create(job)

    # Mutate every mutable field
    updated_at = datetime(2026, 9, 14, 2, 0, 0, tzinfo=UTC)
    started_at = datetime(2026, 9, 14, 2, 5, 0, tzinfo=UTC)
    completed_at = datetime(2026, 9, 14, 2, 30, 0, tzinfo=UTC)
    queued_at = datetime(2026, 9, 14, 1, 30, 0, tzinfo=UTC)
    next_dispatch_at = datetime(2026, 9, 14, 2, 0, 0, tzinfo=UTC)

    new_payload = {"version": 1, "custom_field": "test-data"}
    new_completed_result = {"status": "succeeded", "score": 92.0}

    job.status = AnalysisJobStatus.COMPLETED
    job.last_update_sequence = 7
    job.payload_version = 2
    job.attempts = 3
    job.cancel_requested = True
    job.retry_count = 2
    job.last_error = "safe diagnostic summary"
    job.updated_at = updated_at
    job.started_at = started_at
    job.completed_at = completed_at
    job.payload = new_payload
    job.queued_at = queued_at
    job.next_dispatch_at = next_dispatch_at
    job.dispatch_retry_count = 4
    job.last_dispatch_error_category = "timeout_category"
    job.completed_result = new_completed_result

    # Call repository update
    updated_returned = await repo.update(job)
    assert updated_returned.status == AnalysisJobStatus.COMPLETED

    # Fetch fresh from database and assert every field persisted
    persisted = await repo.get_by_id(job.id)
    assert persisted is not None
    assert persisted.status == AnalysisJobStatus.COMPLETED
    assert persisted.last_update_sequence == 7
    assert persisted.payload_version == 2
    assert persisted.attempts == 3
    assert persisted.cancel_requested is True
    assert persisted.retry_count == 2
    assert persisted.last_error == "safe diagnostic summary"
    assert _to_utc(persisted.updated_at) == updated_at
    assert _to_utc(persisted.started_at) == started_at
    assert _to_utc(persisted.completed_at) == completed_at
    assert persisted.payload == new_payload
    assert _to_utc(persisted.queued_at) == queued_at
    assert _to_utc(persisted.next_dispatch_at) == next_dispatch_at
    assert persisted.dispatch_retry_count == 4
    assert persisted.last_dispatch_error_category == "timeout_category"
    assert persisted.completed_result == new_completed_result


@pytest.mark.anyio
async def test_get_eligible_pending_jobs_batch_and_ordering(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, manifest_id, _ = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

    # Setup multiple attempts for jobs
    attempts: list[UUID] = []
    for i in range(2, 8):
        att_id = uuid4()
        async_db_session.add(
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
        attempts.append(att_id)
    await async_db_session.flush()

    # Job 1: PENDING, next_dispatch_at in the past (11:00) -> ELIGIBLE
    job1 = _make_job(
        session_id,
        attempts[0],
        attempt_number=2,
        status=AnalysisJobStatus.PENDING,
        now=now - timedelta(hours=2),
        next_dispatch_at=now - timedelta(hours=1),
    )
    # Job 2: PENDING, next_dispatch_at is None -> ELIGIBLE (immediate)
    job2 = _make_job(
        session_id,
        attempts[1],
        attempt_number=3,
        status=AnalysisJobStatus.PENDING,
        now=now - timedelta(minutes=90),
        next_dispatch_at=None,
    )
    # Job 3: PENDING, next_dispatch_at in future (13:00) -> NOT ELIGIBLE
    job3 = _make_job(
        session_id,
        attempts[2],
        attempt_number=4,
        status=AnalysisJobStatus.PENDING,
        now=now - timedelta(minutes=30),
        next_dispatch_at=now + timedelta(hours=1),
    )
    # Job 4: PENDING, cancel_requested is True -> NOT ELIGIBLE
    job4 = _make_job(
        session_id,
        attempts[3],
        attempt_number=5,
        status=AnalysisJobStatus.PENDING,
        now=now - timedelta(hours=3),
        next_dispatch_at=now - timedelta(hours=2),
        cancel_requested=True,
    )
    # Job 5: QUEUED -> NOT ELIGIBLE
    job5 = _make_job(
        session_id,
        attempts[4],
        attempt_number=6,
        status=AnalysisJobStatus.QUEUED,
        now=now - timedelta(hours=2),
    )
    # Job 6: PENDING, next_dispatch_at == now (12:00) -> ELIGIBLE
    job6 = _make_job(
        session_id,
        attempts[5],
        attempt_number=7,
        status=AnalysisJobStatus.PENDING,
        now=now - timedelta(minutes=45),
        next_dispatch_at=now,
    )

    for j in [job1, job2, job3, job4, job5, job6]:
        await repo.create(j)

    # Fetch eligible batch with limit 10
    eligible = await repo.get_eligible_pending_jobs(now, limit=10)
    eligible_ids = [j.id for j in eligible]

    # Only job2 (None), job1 (11:00), job6 (12:00) should be included
    assert set(eligible_ids) == {job1.id, job2.id, job6.id}
    assert job3.id not in eligible_ids
    assert job4.id not in eligible_ids
    assert job5.id not in eligible_ids

    # Verify deterministic ordering: None first (job2), then earlier next_dispatch_at (job1)
    assert eligible_ids == [job2.id, job1.id, job6.id]

    # Test bounded limit
    bounded = await repo.get_eligible_pending_jobs(now, limit=2)
    assert len(bounded) == 2
    assert [j.id for j in bounded] == [job2.id, job1.id]


@pytest.mark.anyio
async def test_conditionally_change_pending_to_queued(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, status=AnalysisJobStatus.PENDING, now=now)
    await repo.create(job)

    # 1. Successfully change from PENDING to QUEUED
    queue_time = datetime(2026, 9, 14, 10, 1, 0, tzinfo=UTC)
    new_envelope = {"version": 1, "task": "analyze_session", "dispatched": True}
    changed = await repo.change_pending_to_queued(job.id, queue_time, payload=new_envelope)

    assert changed is not None
    assert changed.status == AnalysisJobStatus.QUEUED
    assert _to_utc(changed.queued_at) == queue_time
    assert _to_utc(changed.updated_at) == queue_time
    assert changed.payload == new_envelope

    # 2. Re-trying on already QUEUED job must return None
    second_try = await repo.change_pending_to_queued(job.id, queue_time)
    assert second_try is None

    # 3. mark_as_queued boolean helper returns False when not pending
    assert await repo.mark_as_queued(job.id, queue_time) is False

    # 4. Non-existent job returns None
    assert await repo.change_pending_to_queued(uuid4(), queue_time) is None


@pytest.mark.anyio
async def test_record_dispatch_failure(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, status=AnalysisJobStatus.PENDING, now=now)
    await repo.create(job)

    # First dispatch failure
    retry1_time = now + timedelta(minutes=1)
    fail_time1 = now + timedelta(seconds=5)
    res1 = await repo.record_dispatch_failure(
        job_id=job.id,
        now=fail_time1,
        next_eligible_at=retry1_time,
        error_category="connection_refused",
        error_message="Cannot connect to broker",
    )
    assert res1 is not None
    assert res1.status == AnalysisJobStatus.PENDING
    assert res1.dispatch_retry_count == 1
    assert _to_utc(res1.next_dispatch_at) == retry1_time
    assert res1.last_dispatch_error_category == "connection_refused"
    assert res1.last_error == "Cannot connect to broker"
    assert _to_utc(res1.updated_at) == fail_time1

    # Second dispatch failure
    retry2_time = now + timedelta(minutes=5)
    fail_time2 = now + timedelta(seconds=10)
    res2 = await repo.record_dispatch_failure(
        job_id=job.id,
        now=fail_time2,
        next_eligible_at=retry2_time,
        error_category="queue_timeout",
        error_message="Broker publish timed out",
    )
    assert res2 is not None
    assert res2.dispatch_retry_count == 2
    assert _to_utc(res2.next_dispatch_at) == retry2_time
    assert res2.last_dispatch_error_category == "queue_timeout"


@pytest.mark.anyio
async def test_conditionally_apply_callback_sequence_ordering(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    t0 = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, status=AnalysisJobStatus.QUEUED, now=t0)
    await repo.create(job)
    assert job.last_update_sequence == 0

    # 1. Apply sequence 1: started
    t1 = t0 + timedelta(seconds=10)
    applied1 = await repo.apply_callback(
        job_id=job.id,
        sequence=1,
        status="started",
        occurred_at=t1,
        payload={"pipeline_version": "0.1.0"},
    )
    assert applied1 is True

    job_after_1 = await repo.get_by_id(job.id)
    assert job_after_1 is not None
    assert job_after_1.status == AnalysisJobStatus.RUNNING
    assert job_after_1.last_update_sequence == 1
    assert _to_utc(job_after_1.started_at) == t1

    # 2. Reject duplicate sequence 1
    dup_applied = await repo.apply_callback(
        job_id=job.id,
        sequence=1,
        status="progress",
        occurred_at=t1 + timedelta(seconds=1),
    )
    assert dup_applied is False

    # 3. Reject older sequence 0
    stale_applied = await repo.apply_callback(
        job_id=job.id,
        sequence=0,
        status="started",
        occurred_at=t1,
    )
    assert stale_applied is False

    # 4. Apply sequence 3 (progress): sequence advances to 3
    t3 = t0 + timedelta(seconds=25)
    applied3 = await repo.apply_callback(
        job_id=job.id,
        sequence=3,
        status="progress",
        occurred_at=t3,
        payload={"stage": "speech", "progress": 0.5},
    )
    assert applied3 is True
    job_after_3 = await repo.get_by_id(job.id)
    assert job_after_3 is not None
    assert job_after_3.last_update_sequence == 3
    assert job_after_3.status == AnalysisJobStatus.RUNNING

    # 5. Out-of-order sequence 2 (older than 3) must be rejected
    t2 = t0 + timedelta(seconds=15)
    out_of_order = await repo.apply_callback(
        job_id=job.id,
        sequence=2,
        status="progress",
        occurred_at=t2,
    )
    assert out_of_order is False

    # 6. Apply sequence 4: completed
    t4 = t0 + timedelta(seconds=40)
    completion_payload = {
        "analysis_artifact": {"artifact_id": "art-1", "object_key": "ai/1.json"},
        "primary_questions": [{"text": "Q1?"}, {"text": "Q2?"}, {"text": "Q3?"}],
    }
    applied4 = await repo.apply_callback(
        job_id=job.id,
        sequence=4,
        status="completed",
        occurred_at=t4,
        payload=completion_payload,
    )
    assert applied4 is True

    completed_job = await repo.get_by_id(job.id)
    assert completed_job is not None
    assert completed_job.status == AnalysisJobStatus.COMPLETED
    assert completed_job.last_update_sequence == 4
    assert _to_utc(completed_job.completed_at) == t4
    assert completed_job.completed_result == completion_payload


@pytest.mark.anyio
async def test_apply_callback_terminal_and_error_states(
    async_db_session: AsyncSession,
) -> None:
    _, _, _, session_id, _, attempt_id = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    t0 = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, status=AnalysisJobStatus.RUNNING, now=t0)
    await repo.create(job)

    # Failure update
    t1 = t0 + timedelta(seconds=20)
    fail_res = await repo.apply_callback_update(
        job_id=job.id,
        sequence=1,
        status="failed",
        occurred_at=t1,
        error_message="Model inference failure",
        attempts=2,
    )
    assert fail_res is not None
    assert fail_res.status == AnalysisJobStatus.FAILED
    assert fail_res.last_update_sequence == 1
    assert fail_res.last_error == "Model inference failure"
    assert fail_res.attempts == 2
    assert _to_utc(fail_res.completed_at) == t1

    # Cancellation update with sequence 2
    t2 = t0 + timedelta(seconds=30)
    cancel_res = await repo.apply_callback_update(
        job_id=job.id,
        sequence=2,
        status="cancelled",
        occurred_at=t2,
    )
    assert cancel_res is not None
    assert cancel_res.status == AnalysisJobStatus.CANCELLED
    assert cancel_res.last_update_sequence == 2
    assert cancel_res.cancel_requested is True


@pytest.mark.anyio
async def test_loading_ancestry_and_current_attempt_context(
    async_db_session: AsyncSession,
) -> None:
    (
        user_id,
        team_id,
        project_id,
        session_id,
        manifest_id,
        attempt_id,
    ) = await _create_test_context_entities(async_db_session)
    repo = SqlAlchemyAnalysisJobRepository(async_db_session)

    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
    job = _make_job(session_id, attempt_id, now=now)
    await repo.create(job)

    context = await repo.get_ancestry_context(job.id)
    assert context is not None
    assert context.job_id == job.id
    assert context.attempt_id == attempt_id
    assert context.session_id == session_id
    assert context.project_id == project_id
    assert context.team_id == team_id
    assert context.manifest_id == manifest_id
    assert context.attempt_number == 1
    assert context.manifest is not None
    assert context.manifest.rubric_id == "startup_pitch"
    assert context.manifest.rubric_version == 1

    # Also test by attempt id
    context_by_attempt = await repo.get_ancestry_context_by_attempt_id(attempt_id)
    assert context_by_attempt is not None
    assert context_by_attempt.job_id == job.id
    assert context_by_attempt.team_id == team_id

    # Non-existent job
    assert await repo.get_ancestry_context(uuid4()) is None
