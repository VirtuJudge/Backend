from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AnalyzeSessionPayload,
    AssetInput,
    RubricRef,
)
from app.application.ai_jobs import AIJobs, RedispatchResult, _calculate_backoff_delay
from app.application.ports.ai_queue import AIQueueTemporaryFailure
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from tests.support import FakeAIJobQueue, FakeAnalysisJobRepository, FakeUnitOfWork


def _make_job(
    *,
    job_id: UUID | None = None,
    practice_session_id: UUID | None = None,
    attempt_id: UUID | None = None,
    analysis_attempt: int = 1,
    status: AnalysisJobStatus = AnalysisJobStatus.PENDING,
    created_at: datetime | None = None,
    next_dispatch_at: datetime | None = None,
    dispatch_retry_count: int = 0,
    last_dispatch_error_category: str | None = None,
    cancel_requested: bool = False,
    payload: dict[str, Any] | None = None,
) -> AnalysisJob:
    jid = job_id or uuid4()
    pid = practice_session_id or uuid4()
    aid = attempt_id or uuid4()
    cid = uuid4()
    now = created_at or datetime.now(UTC)
    if payload is None:
        envelope = AIJobQueueMessage(
            schema_version=1,
            job_id=str(jid),
            job_type=AIJobType.ANALYZE_SESSION,
            practice_session_id=str(pid),
            analysis_attempt=analysis_attempt,
            created_at=now,
            trace_id=f"trc_{cid.hex}",
            payload=AnalyzeSessionPayload(
                presentation=AssetInput(
                    artifact_id=str(uuid4()),
                    object_key="assets/sample.mp4",
                    checksum="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    media_type="video/mp4",
                ),
                rubric=RubricRef(rubric_id="pitch", version=1),
                requested_capabilities=["speech"],
            ),
        )
        payload = envelope.model_dump(mode="json", exclude_none=True)

    return AnalysisJob(
        id=jid,
        practice_session_id=pid,
        attempt_id=aid,
        analysis_attempt=analysis_attempt,
        job_type="analyze_session",
        status=status,
        correlation_id=cid,
        last_update_sequence=0,
        payload_version=1,
        attempts=0,
        cancel_requested=cancel_requested,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        payload=payload,
        queued_at=None,
        next_dispatch_at=next_dispatch_at,
        dispatch_retry_count=dispatch_retry_count,
        last_dispatch_error_category=last_dispatch_error_category,
        completed_result=None,
    )


def test_calculate_backoff_delay() -> None:
    assert _calculate_backoff_delay(1, base_seconds=30.0, factor=2.0, max_seconds=3600.0) == 30.0
    assert _calculate_backoff_delay(2, base_seconds=30.0, factor=2.0, max_seconds=3600.0) == 60.0
    assert _calculate_backoff_delay(3, base_seconds=30.0, factor=2.0, max_seconds=3600.0) == 120.0
    assert _calculate_backoff_delay(10, base_seconds=30.0, factor=2.0, max_seconds=3600.0) == 3600.0
    assert _calculate_backoff_delay(1, explicit_retry_after=45.0, max_seconds=3600.0) == 45.0
    assert _calculate_backoff_delay(1, explicit_retry_after=5000.0, max_seconds=3600.0) == 3600.0


def test_redispatch_result_type_coercion() -> None:
    res = RedispatchResult(processed=3, queued=3, failed=0)
    assert int(res) == 3
    assert bool(res) is True
    assert res == 3
    assert res == RedispatchResult(3, 3, 0)

    empty = RedispatchResult(0, 0, 0)
    assert int(empty) == 0
    assert bool(empty) is False
    assert empty == 0


@pytest.mark.asyncio
async def test_redispatch_pending_bounded_batch() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    jobs = [_make_job(created_at=base_time + timedelta(minutes=i)) for i in range(15)]
    repo = FakeAnalysisJobRepository(jobs)
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    res = await ai_jobs.redispatch_pending(limit=5, now=base_time + timedelta(hours=1))
    assert res.processed == 5
    assert res.queued == 5
    assert res.failed == 0
    assert queue.count == 5

    res2 = await ai_jobs.redispatch_pending(batch_size=3, now=base_time + timedelta(hours=1))
    assert res2.processed == 3
    assert res2.queued == 3
    assert res2.failed == 0
    assert queue.count == 8


@pytest.mark.asyncio
async def test_redispatch_pending_oldest_eligible_first() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job_old = _make_job(created_at=base_time)
    job_mid = _make_job(created_at=base_time + timedelta(minutes=10))
    job_new = _make_job(created_at=base_time + timedelta(minutes=20))

    repo = FakeAnalysisJobRepository([job_new, job_old, job_mid])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    now = base_time + timedelta(minutes=30)
    res = await ai_jobs.redispatch_pending(limit=10, now=now)
    assert res.queued == 3

    assert [msg.job_id for msg in queue.published_messages] == [
        str(job_old.id),
        str(job_mid.id),
        str(job_new.id),
    ]


@pytest.mark.asyncio
async def test_redispatch_pending_preserves_stable_queue_envelope() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job = _make_job(created_at=base_time)
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    now = base_time + timedelta(minutes=5)
    res = await ai_jobs.redispatch_pending(now=now)
    assert res.queued == 1

    msg = queue.last_message
    assert msg is not None
    assert str(msg.job_id) == str(job.id)
    assert str(msg.practice_session_id) == str(job.practice_session_id)
    assert msg.schema_version == 1
    assert msg.job_type == AIJobType.ANALYZE_SESSION


@pytest.mark.asyncio
async def test_redispatch_pending_marks_accepted_jobs_queued() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job = _make_job(created_at=base_time)
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    now = base_time + timedelta(minutes=5)
    res = await ai_jobs.redispatch_pending(now=now)
    assert res.queued == 1

    persisted = await repo.get_by_id(job.id)
    assert persisted is not None
    assert persisted.status == AnalysisJobStatus.QUEUED
    assert persisted.queued_at == now
    assert persisted.updated_at == now


@pytest.mark.asyncio
async def test_redispatch_pending_leaves_failed_jobs_pending_with_retry_metadata() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job = _make_job(created_at=base_time)
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    queue.fail_next(1, AIQueueTemporaryFailure("Connection timeout"))
    ai_jobs = AIJobs(uow, queue=queue)

    now = base_time + timedelta(minutes=5)
    res = await ai_jobs.redispatch_pending(
        now=now,
        base_backoff_seconds=30.0,
        backoff_factor=2.0,
    )
    assert res.processed == 1
    assert res.queued == 0
    assert res.failed == 1

    persisted = await repo.get_by_id(job.id)
    assert persisted is not None
    assert persisted.status == AnalysisJobStatus.PENDING
    assert persisted.dispatch_retry_count == 1
    assert persisted.last_dispatch_error_category == "temporary_queue_failure"
    assert persisted.last_error == "Connection timeout"
    assert persisted.next_dispatch_at == now + timedelta(seconds=30.0)


@pytest.mark.asyncio
async def test_redispatch_pending_exponential_backoff_and_clock_injection() -> None:
    current_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

    def clock() -> datetime:
        return current_time

    job = _make_job(created_at=current_time)
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    # 1st failure
    queue.fail_next(1, AIQueueTemporaryFailure("Err 1"))
    res1 = await ai_jobs.redispatch_pending(
        clock=clock,
        base_backoff_seconds=10.0,
        backoff_factor=2.0,
    )
    assert res1.failed == 1
    job1 = await repo.get_by_id(job.id)
    assert job1 is not None
    assert job1.dispatch_retry_count == 1
    assert job1.next_dispatch_at == current_time + timedelta(seconds=10.0)

    # Before next_dispatch_at: not eligible
    current_time += timedelta(seconds=5.0)
    res_early = await ai_jobs.redispatch_pending(clock=clock)
    assert res_early.processed == 0

    # At next_dispatch_at: 2nd failure
    current_time += timedelta(seconds=5.0)
    queue.fail_next(1, AIQueueTemporaryFailure("Err 2"))
    res2 = await ai_jobs.redispatch_pending(
        clock=clock,
        base_backoff_seconds=10.0,
        backoff_factor=2.0,
    )
    assert res2.failed == 1
    job2 = await repo.get_by_id(job.id)
    assert job2 is not None
    assert job2.dispatch_retry_count == 2
    assert job2.next_dispatch_at == current_time + timedelta(seconds=20.0)

    # 3rd failure with capped backoff
    current_time += timedelta(seconds=20.0)
    queue.fail_next(1, AIQueueTemporaryFailure("Err 3"))
    res3 = await ai_jobs.redispatch_pending(
        clock=clock,
        base_backoff_seconds=10.0,
        backoff_factor=2.0,
        max_backoff_seconds=25.0,
    )
    assert res3.failed == 1
    job3 = await repo.get_by_id(job.id)
    assert job3 is not None
    assert job3.dispatch_retry_count == 3
    # 10 * 4 = 40, capped at 25.0
    assert job3.next_dispatch_at == current_time + timedelta(seconds=25.0)


@pytest.mark.asyncio
async def test_redispatch_pending_explicit_retry_after() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job = _make_job(created_at=base_time)
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    queue.fail_next(1, AIQueueTemporaryFailure("Rate limited", retry_after_seconds=45.0))
    ai_jobs = AIJobs(uow, queue=queue)

    res = await ai_jobs.redispatch_pending(now=base_time, base_backoff_seconds=10.0)
    assert res.failed == 1

    persisted = await repo.get_by_id(job.id)
    assert persisted is not None
    assert persisted.next_dispatch_at == base_time + timedelta(seconds=45.0)


@pytest.mark.asyncio
async def test_redispatch_pending_continues_when_one_publish_fails() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job1 = _make_job(created_at=base_time)
    job2 = _make_job(created_at=base_time + timedelta(minutes=1))
    job3 = _make_job(created_at=base_time + timedelta(minutes=2))

    repo = FakeAnalysisJobRepository([job1, job2, job3])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    # Fail only job2
    queue.fail_if(
        lambda env: str(env.job_id) == str(job2.id),
        AIQueueTemporaryFailure("Job2 broker fail"),
    )
    ai_jobs = AIJobs(uow, queue=queue)

    now = base_time + timedelta(minutes=5)
    res = await ai_jobs.redispatch_pending(limit=10, now=now)
    assert res.processed == 3
    assert res.queued == 2
    assert res.failed == 1

    # Queue received job1 and job3
    assert queue.count == 2
    assert [msg.job_id for msg in queue.published_messages] == [str(job1.id), str(job3.id)]

    p1 = await repo.get_by_id(job1.id)
    p2 = await repo.get_by_id(job2.id)
    p3 = await repo.get_by_id(job3.id)

    assert p1 is not None and p1.status == AnalysisJobStatus.QUEUED
    assert p2 is not None and p2.status == AnalysisJobStatus.PENDING
    assert p2.dispatch_retry_count == 1
    assert p3 is not None and p3.status == AnalysisJobStatus.QUEUED


@pytest.mark.asyncio
async def test_redispatch_pending_no_queue_configured() -> None:
    job = _make_job()
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    ai_jobs = AIJobs(uow, queue=None)

    res = await ai_jobs.redispatch_pending()
    assert res.processed == 0
    assert res.queued == 0
    assert res.failed == 0


@pytest.mark.asyncio
async def test_redispatch_pending_skips_ineligible_jobs() -> None:
    base_time = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    job_future = _make_job(
        created_at=base_time,
        next_dispatch_at=base_time + timedelta(hours=1),
    )
    job_cancelled = _make_job(
        created_at=base_time,
        cancel_requested=True,
    )
    job_queued = _make_job(
        created_at=base_time,
        status=AnalysisJobStatus.QUEUED,
    )
    repo = FakeAnalysisJobRepository([job_future, job_cancelled, job_queued])
    uow = FakeUnitOfWork(repo)
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    res = await ai_jobs.redispatch_pending(now=base_time)
    assert res.processed == 0
    assert res.queued == 0
    assert res.failed == 0
    assert queue.count == 0


@pytest.mark.asyncio
async def test_ai_jobs_get_job_and_get_job_status() -> None:
    job = _make_job()
    repo = FakeAnalysisJobRepository([job])
    uow = FakeUnitOfWork(repo)
    ai_jobs = AIJobs(uow)

    retrieved = await ai_jobs.get_job(job.id)
    assert retrieved is not None
    assert retrieved.id == job.id

    status_retrieved = await ai_jobs.get_job_status(job.id)
    assert status_retrieved is not None
    assert status_retrieved.id == job.id

    assert await ai_jobs.get_job(uuid4()) is None
    assert await ai_jobs.get_job_status(uuid4()) is None
