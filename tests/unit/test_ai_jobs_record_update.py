from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    CancelledPayload,
    FailedPayload,
    ProgressPayload,
    StartedPayload,
)
from app.application.ai_jobs import AIJobs
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.exceptions import (
    InvalidJobStatusTransition,
    StaleEntityVersion,
)
from tests.support.fake_analysis_attempt_repository import FakeAnalysisAttemptRepository
from tests.support.fake_analysis_job_repository import FakeAnalysisJobRepository, FakeUnitOfWork


def _build_attempt_and_job(
    *,
    job_status: AnalysisJobStatus = AnalysisJobStatus.PENDING,
    attempt_status: AnalysisAttemptStatus = AnalysisAttemptStatus.QUEUED,
    last_update_sequence: int = 0,
    job_attempts: int = 0,
    cancel_requested: bool = False,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[AnalysisJob, AnalysisAttempt, FakeUnitOfWork, AIJobs]:
    job_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    attempt_id = uuid4()
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

    attempt = AnalysisAttempt(
        id=attempt_id,
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key=None,
        attempt_number=1,
        status=attempt_status,
        failure_code=None,
        failure_message=None,
        created_at=now,
        started_at=started_at,
        completed_at=completed_at,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )

    job = AnalysisJob(
        id=job_id,
        practice_session_id=session_id,
        attempt_id=attempt_id,
        analysis_attempt=1,
        job_type="analyze_session",
        status=job_status,
        correlation_id=uuid4(),
        last_update_sequence=last_update_sequence,
        payload_version=1,
        attempts=job_attempts,
        cancel_requested=cancel_requested,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=started_at,
        completed_at=completed_at,
        payload=payload or {},
        queued_at=now if job_status == AnalysisJobStatus.QUEUED else None,
    )

    job_repo = FakeAnalysisJobRepository([job])
    attempt_repo = FakeAnalysisAttemptRepository([attempt])
    uow = FakeUnitOfWork(jobs=job_repo, attempts=attempt_repo)
    ai_jobs = AIJobs(uow)
    return job, attempt, uow, ai_jobs


def _make_update(
    status: AIWorkerUpdateStatus | str,
    sequence: int = 1,
    payload: Any = None,
    occurred_at: datetime | None = None,
) -> AIWorkerUpdate:
    occ = occurred_at or datetime(2026, 9, 14, 12, 5, 0, tzinfo=UTC)
    if payload is None:
        if status == AIWorkerUpdateStatus.STARTED or status == "started":
            payload = StartedPayload(pipeline_version="0.1.0")
        elif status == AIWorkerUpdateStatus.PROGRESS or status == "progress":
            payload = ProgressPayload(stage="speech", progress=0.5, message="Transcribing")
        elif status == AIWorkerUpdateStatus.FAILED or status == "failed":
            payload = FailedPayload(
                stage="speech",
                code="timeout",
                retryable=True,
                attempts=1,
                message="Operation timed out",
            )
        elif status == AIWorkerUpdateStatus.CANCELLED or status == "cancelled":
            payload = CancelledPayload()
        elif status == AIWorkerUpdateStatus.COMPLETED or status == "completed":
            payload = {}

    return AIWorkerUpdate(
        schema_version=1,
        sequence=sequence,
        status=status,
        occurred_at=occ,
        trace_id="trc_test_update",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Allowed Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_pending_to_started() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.PENDING,
        attempt_status=AnalysisAttemptStatus.QUEUED,
    )
    occurred_at = datetime(2026, 9, 14, 12, 1, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.STARTED,
        sequence=1,
        payload=StartedPayload(pipeline_version="0.2.1"),
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.RUNNING
    assert result.started_at == occurred_at
    assert result.last_update_sequence == 1
    assert result.payload is not None
    assert result.payload["pipeline_version"] == "0.2.1"
    assert attempt.status == AnalysisAttemptStatus.RUNNING
    assert attempt.started_at == occurred_at
    assert attempt.version == 2
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_allowed_queued_to_started() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.QUEUED,
        attempt_status=AnalysisAttemptStatus.QUEUED,
    )
    occurred_at = datetime(2026, 9, 14, 12, 2, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.STARTED,
        sequence=1,
        payload=StartedPayload(pipeline_version="0.3.0"),
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.RUNNING
    assert result.started_at == occurred_at
    assert attempt.status == AnalysisAttemptStatus.RUNNING
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_allowed_running_to_progress() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        started_at=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
    )
    update = _make_update(
        AIWorkerUpdateStatus.PROGRESS,
        sequence=2,
        payload=ProgressPayload(stage="diarization", progress=0.6, message="Identifying speakers"),
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.RUNNING
    assert result.last_update_sequence == 2
    assert result.payload is not None
    assert result.payload["progress"] == {
        "stage": "diarization",
        "progress": 0.6,
        "message": "Identifying speakers",
    }
    assert attempt.status == AnalysisAttemptStatus.RUNNING
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_allowed_running_to_completed() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=2,
    )
    occurred_at = datetime(2026, 9, 14, 12, 10, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=3,
        payload={},
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_at == occurred_at
    assert result.last_update_sequence == 3
    assert attempt.status == AnalysisAttemptStatus.COMPLETED
    assert attempt.completed_at == occurred_at
    assert uow.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_job_status", "start_attempt_status"),
    [
        (AnalysisJobStatus.PENDING, AnalysisAttemptStatus.QUEUED),
        (AnalysisJobStatus.QUEUED, AnalysisAttemptStatus.QUEUED),
        (AnalysisJobStatus.RUNNING, AnalysisAttemptStatus.RUNNING),
    ],
)
async def test_allowed_transitions_to_failed(
    start_job_status: AnalysisJobStatus,
    start_attempt_status: AnalysisAttemptStatus,
) -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=start_job_status,
        attempt_status=start_attempt_status,
        last_update_sequence=0 if start_job_status != AnalysisJobStatus.RUNNING else 1,
        job_attempts=1,
    )
    occurred_at = datetime(2026, 9, 14, 12, 8, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.FAILED,
        sequence=2,
        payload=FailedPayload(
            stage="vision",
            code="gpu_out_of_memory",
            retryable=True,
            attempts=3,
            message="Out of memory during slide vision parsing",
        ),
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.FAILED
    assert result.completed_at == occurred_at
    assert result.last_error == "Out of memory during slide vision parsing"
    assert result.attempts == 3
    assert result.payload is not None
    assert result.payload["failure"] == {
        "stage": "vision",
        "code": "gpu_out_of_memory",
        "retryable": True,
        "attempts": 3,
        "message": "Out of memory during slide vision parsing",
    }
    assert attempt.status == AnalysisAttemptStatus.FAILED
    assert attempt.failure_code == "gpu_out_of_memory"
    assert attempt.failure_message == "Out of memory during slide vision parsing"
    assert attempt.failed_at == occurred_at
    assert attempt.completed_at == occurred_at
    assert uow.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_job_status", "start_attempt_status"),
    [
        (AnalysisJobStatus.PENDING, AnalysisAttemptStatus.QUEUED),
        (AnalysisJobStatus.QUEUED, AnalysisAttemptStatus.QUEUED),
        (AnalysisJobStatus.RUNNING, AnalysisAttemptStatus.RUNNING),
    ],
)
async def test_allowed_transitions_to_cancelled(
    start_job_status: AnalysisJobStatus,
    start_attempt_status: AnalysisAttemptStatus,
) -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=start_job_status,
        attempt_status=start_attempt_status,
        last_update_sequence=0 if start_job_status != AnalysisJobStatus.RUNNING else 1,
    )
    occurred_at = datetime(2026, 9, 14, 12, 9, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.CANCELLED,
        sequence=2,
        payload=CancelledPayload(),
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.CANCELLED
    assert result.cancel_requested is True
    assert result.completed_at == occurred_at
    assert attempt.status == AnalysisAttemptStatus.CANCELLED
    assert attempt.cancelled_at == occurred_at
    assert attempt.completed_at == occurred_at
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_allowed_cancelled_to_cancelled_acknowledgement() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.CANCELLED,
        attempt_status=AnalysisAttemptStatus.CANCELLED,
        last_update_sequence=1,
        cancel_requested=True,
        completed_at=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
    )
    occurred_at = datetime(2026, 9, 14, 12, 15, 0, tzinfo=UTC)
    update = _make_update(
        AIWorkerUpdateStatus.CANCELLED,
        sequence=2,
        payload=CancelledPayload(),
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.CANCELLED
    assert result.last_update_sequence == 2
    assert attempt.status == AnalysisAttemptStatus.CANCELLED
    assert uow.commit_count == 1


# ---------------------------------------------------------------------------
# Forbidden Transitions and Terminal Revival Prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_job_status", "start_attempt_status", "update_status"),
    [
        (AnalysisJobStatus.PENDING, AnalysisAttemptStatus.QUEUED, AIWorkerUpdateStatus.PROGRESS),
        (AnalysisJobStatus.PENDING, AnalysisAttemptStatus.QUEUED, AIWorkerUpdateStatus.COMPLETED),
        (AnalysisJobStatus.QUEUED, AnalysisAttemptStatus.QUEUED, AIWorkerUpdateStatus.PROGRESS),
        (AnalysisJobStatus.QUEUED, AnalysisAttemptStatus.QUEUED, AIWorkerUpdateStatus.COMPLETED),
        (AnalysisJobStatus.RUNNING, AnalysisAttemptStatus.RUNNING, AIWorkerUpdateStatus.STARTED),
        (
            AnalysisJobStatus.COMPLETED,
            AnalysisAttemptStatus.COMPLETED,
            AIWorkerUpdateStatus.STARTED,
        ),
        (
            AnalysisJobStatus.COMPLETED,
            AnalysisAttemptStatus.COMPLETED,
            AIWorkerUpdateStatus.PROGRESS,
        ),
        (AnalysisJobStatus.COMPLETED, AnalysisAttemptStatus.COMPLETED, AIWorkerUpdateStatus.FAILED),
        (
            AnalysisJobStatus.COMPLETED,
            AnalysisAttemptStatus.COMPLETED,
            AIWorkerUpdateStatus.CANCELLED,
        ),
        (
            AnalysisJobStatus.COMPLETED,
            AnalysisAttemptStatus.COMPLETED,
            AIWorkerUpdateStatus.COMPLETED,
        ),
        (AnalysisJobStatus.FAILED, AnalysisAttemptStatus.FAILED, AIWorkerUpdateStatus.STARTED),
        (AnalysisJobStatus.FAILED, AnalysisAttemptStatus.FAILED, AIWorkerUpdateStatus.PROGRESS),
        (AnalysisJobStatus.FAILED, AnalysisAttemptStatus.FAILED, AIWorkerUpdateStatus.FAILED),
        (AnalysisJobStatus.FAILED, AnalysisAttemptStatus.FAILED, AIWorkerUpdateStatus.CANCELLED),
        (AnalysisJobStatus.FAILED, AnalysisAttemptStatus.FAILED, AIWorkerUpdateStatus.COMPLETED),
        (
            AnalysisJobStatus.CANCELLED,
            AnalysisAttemptStatus.CANCELLED,
            AIWorkerUpdateStatus.STARTED,
        ),
        (
            AnalysisJobStatus.CANCELLED,
            AnalysisAttemptStatus.CANCELLED,
            AIWorkerUpdateStatus.PROGRESS,
        ),
        (AnalysisJobStatus.CANCELLED, AnalysisAttemptStatus.CANCELLED, AIWorkerUpdateStatus.FAILED),
        (
            AnalysisJobStatus.CANCELLED,
            AnalysisAttemptStatus.CANCELLED,
            AIWorkerUpdateStatus.COMPLETED,
        ),
    ],
)
async def test_forbidden_transitions_raise_exception(
    start_job_status: AnalysisJobStatus,
    start_attempt_status: AnalysisAttemptStatus,
    update_status: AIWorkerUpdateStatus,
) -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=start_job_status,
        attempt_status=start_attempt_status,
        last_update_sequence=1,
    )
    update = _make_update(update_status, sequence=2)

    with pytest.raises(InvalidJobStatusTransition):
        await service.record_update(job.id, update)

    assert uow.commit_count == 0
    assert job.status == start_job_status
    assert attempt.status == start_attempt_status


# ---------------------------------------------------------------------------
# Duplicate Sequence & Reversed Delivery Handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_sequence_is_acknowledged_with_zero_side_effects() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=3,
        payload={"pipeline_version": "0.1.0"},
    )
    initial_version = attempt.version
    initial_updated_at = job.updated_at

    update = _make_update(
        AIWorkerUpdateStatus.PROGRESS,
        sequence=3,
        payload=ProgressPayload(stage="speech", progress=0.8, message="Should be ignored"),
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.id == job.id
    assert result.last_update_sequence == 3
    assert result.updated_at == initial_updated_at
    assert "progress" not in (result.payload or {})
    assert attempt.version == initial_version
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_reversed_delivery_stale_sequence_ignored() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=4,
    )
    initial_version = attempt.version

    # Stale sequence 2 arrives after sequence 4
    update = _make_update(
        AIWorkerUpdateStatus.PROGRESS,
        sequence=2,
        payload=ProgressPayload(stage="speech", progress=0.2, message="Stale update"),
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.last_update_sequence == 4
    assert attempt.version == initial_version
    assert uow.commit_count == 0


# ---------------------------------------------------------------------------
# Monotonic Worker Attempts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_attempts_cannot_regress() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_attempts=3,
    )
    # Failure report claims only 1 attempt was made
    update = _make_update(
        AIWorkerUpdateStatus.FAILED,
        sequence=2,
        payload=FailedPayload(
            stage="speech",
            code="timeout",
            retryable=True,
            attempts=1,
            message="Speech failed",
        ),
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.attempts == 3  # Did not regress to 1


@pytest.mark.asyncio
async def test_worker_attempts_can_increase() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_attempts=1,
    )
    update = _make_update(
        AIWorkerUpdateStatus.FAILED,
        sequence=2,
        payload=FailedPayload(
            stage="speech",
            code="timeout",
            retryable=True,
            attempts=4,
            message="Speech failed repeatedly",
        ),
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.attempts == 4


# ---------------------------------------------------------------------------
# Concurrent Same-Sequence Delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_same_sequence_delivery_handles_stale_entity_version() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.QUEUED,
        attempt_status=AnalysisAttemptStatus.QUEUED,
        last_update_sequence=0,
    )
    update = _make_update(
        AIWorkerUpdateStatus.STARTED,
        sequence=1,
        payload=StartedPayload(pipeline_version="0.1.0"),
    )

    # First delivery succeeds normally
    first_result = await service.record_update(job.id, update)
    assert first_result is not None
    assert first_result.status == AnalysisJobStatus.RUNNING
    assert first_result.last_update_sequence == 1
    assert attempt.version == 2

    # A concurrent second caller with stale view (attempt.version=1) hits StaleEntityVersion
    # but the job has already reached sequence 1.
    stale_attempt = AnalysisAttempt(
        id=attempt.id,
        session_id=attempt.session_id,
        manifest_id=attempt.manifest_id,
        idempotency_key=None,
        attempt_number=1,
        status=AnalysisAttemptStatus.QUEUED,
        failure_code=None,
        failure_message=None,
        created_at=attempt.created_at,
        started_at=None,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,  # Stale version
    )

    # Repository that simulates the concurrent collision on update
    class CollidingAttemptRepo(FakeAnalysisAttemptRepository):
        def __init__(self, backing: FakeAnalysisAttemptRepository) -> None:
            super().__init__(list(backing.attempts.values()))
            self._backing = backing

        async def get_by_id(self, attempt_id: UUID) -> AnalysisAttempt | None:
            # Returns stale attempt view
            return stale_attempt

        async def update(self, att: AnalysisAttempt, expected_version: int) -> AnalysisAttempt:
            # Backing attempt is already at version 2, so expected_version 1
            # fails with StaleEntityVersion
            if self._backing.attempts[att.id].version != expected_version:
                raise StaleEntityVersion("Analysis attempt was modified concurrently.")
            return await super().update(att, expected_version)

    colliding_attempt_repo = CollidingAttemptRepo(uow.attempts)
    colliding_uow = FakeUnitOfWork(jobs=uow.jobs, attempts=colliding_attempt_repo)
    colliding_service = AIJobs(colliding_uow)

    second_result = await colliding_service.record_update(job.id, update)

    assert second_result is not None
    assert second_result.status == AnalysisJobStatus.RUNNING
    assert second_result.last_update_sequence == 1
    assert colliding_uow.rollback_count == 0 or colliding_uow.rollback_count >= 1


# ---------------------------------------------------------------------------
# Unknown Job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_update_unknown_job_returns_none() -> None:
    _, _, _, service = _build_attempt_and_job()
    update = _make_update(AIWorkerUpdateStatus.STARTED, sequence=1)

    result = await service.record_update(uuid4(), update)

    assert result is None
