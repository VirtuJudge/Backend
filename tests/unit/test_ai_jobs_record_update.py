from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    AnswerAnalysisCompletedPayload,
    ArtifactRef,
    CancelledPayload,
    ErasureCompletedPayload,
    FailedPayload,
    FollowUpQuestion,
    PrimaryQuestion,
    ProgressPayload,
    ReportCompletedPayload,
    SessionAnalysisCompletedPayload,
    StartedPayload,
)
from app.application.ai_jobs import AIJobs
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    CompletedResultValidationError,
    InvalidJobStatusTransition,
    StaleEntityVersion,
)
from tests.support.fake_analysis_attempt_repository import FakeAnalysisAttemptRepository
from tests.support.fake_analysis_job_repository import FakeAnalysisJobRepository, FakeUnitOfWork


def _valid_completed_session_payload() -> SessionAnalysisCompletedPayload:
    return SessionAnalysisCompletedPayload(
        analysis_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000051",
            object_key="artifacts/session_analysis.json",
            checksum="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            schema_version=1,
        ),
        primary_questions=[
            PrimaryQuestion(
                candidate_id="cand_001",
                text="How do you calculate your customer acquisition cost?",
                reason="Clarifies unit economics model",
                rubric_dimension="market_and_business_model",
                evidence_ids=["ev_speech_01", "ev_slide_03"],
            ),
            PrimaryQuestion(
                candidate_id="cand_002",
                text="What is the technical moat protecting your algorithms?",
                reason="Evaluates defensive IP claims",
                rubric_dimension="technology_and_moat",
                evidence_ids=["ev_slide_06"],
            ),
            PrimaryQuestion(
                candidate_id="cand_003",
                text="What milestones will prove enterprise pilot conversion?",
                reason="Assesses go-to-market execution timeline",
                rubric_dimension="execution_and_milestones",
                evidence_ids=["ev_slide_09", "ev_speech_04"],
            ),
        ],
        speaker_labels=["SPEAKER_00", "SPEAKER_01"],
        limitations=[],
    )


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
    job_type: str = "analyze_session",
    attempt_number: int = 1,
    trace_id: str = "trc_test_update",
) -> tuple[AnalysisJob, AnalysisAttempt, FakeUnitOfWork, AIJobs]:
    job_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    attempt_id = uuid4()
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

    job_payload = dict(payload) if payload is not None else {}
    if "trace_id" not in job_payload and trace_id:
        job_payload["trace_id"] = trace_id

    attempt = AnalysisAttempt(
        id=attempt_id,
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key=None,
        attempt_number=attempt_number,
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
        analysis_attempt=attempt_number,
        job_type=job_type,
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
        payload=job_payload,
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
    trace_id: str = "trc_test_update",
    schema_version: int = 1,
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
            payload = _valid_completed_session_payload()

    return AIWorkerUpdate(
        schema_version=schema_version,
        sequence=sequence,
        status=status,
        occurred_at=occ,
        trace_id=trace_id,
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
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_at == occurred_at
    assert result.last_update_sequence == 3
    assert result.completed_result is not None
    assert len(result.completed_result["primary_questions"]) == 3
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


# ===========================================================================
# Completed Callback Handling Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_completed_valid_analyze_session() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    occurred_at = datetime(2026, 9, 14, 12, 10, 0, tzinfo=UTC)
    payload = _valid_completed_session_payload()
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
        occurred_at=occurred_at,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_at == occurred_at
    assert result.last_update_sequence == 2
    assert result.completed_result is not None
    assert (
        result.completed_result["analysis_artifact"]["object_key"]
        == "artifacts/session_analysis.json"
    )
    assert len(result.completed_result["primary_questions"]) == 3
    assert attempt.status == AnalysisAttemptStatus.COMPLETED
    assert attempt.completed_at == occurred_at
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_completed_wrong_result_type() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_type="analyze_session",
    )
    wrong_payload = AnswerAnalysisCompletedPayload(
        answer_id="ans_123",
        transcript_artifact_id="art_trans_1",
        assessment_artifact_id="art_assess_1",
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=wrong_payload,
    )

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "Completed payload validation failed for job_type 'analyze_session'" in str(
        exc_info.value
    )
    assert job.status == AnalysisJobStatus.RUNNING
    assert attempt.status == AnalysisAttemptStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_trace_mismatch() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        trace_id="trc_dispatched_exact",
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        trace_id="trc_foreign_worker",
    )

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "does not match dispatched job trace_id" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_ancestry_mismatch_missing_context() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    assert isinstance(uow.jobs, FakeAnalysisJobRepository)
    uow.jobs.set_ancestry_context(job.id, None)

    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "ancestry context could not be resolved" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_ancestry_mismatch_session_id() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    wrong_session = PracticeSession(
        id=uuid4(),
        project_id=uuid4(),
        created_by=uuid4(),
        name="Different Session",
        status=SessionStatus.ANALYZING,
        version=1,
        created_at=job.created_at,
        updated_at=job.updated_at,
        consent_granted=True,
        started_at=job.started_at,
        completed_at=None,
        cancelled_at=None,
    )
    ancestry = AIJobAncestryContext(
        job=job,
        attempt=attempt,
        session=wrong_session,
        project_id=wrong_session.project_id,
        team_id=uuid4(),
    )
    assert isinstance(uow.jobs, FakeAnalysisJobRepository)
    uow.jobs.set_ancestry_context(job.id, ancestry)

    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "does not match session ancestry" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_invalid_artifact_checksum() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload_dict = _valid_completed_session_payload().model_dump(mode="json")
    payload_dict["analysis_artifact"]["checksum"] = "sha256:invalid_checksum_not_64_hex"
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload_dict)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "checksum" in str(exc_info.value).lower()
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_invalid_artifact_schema_version() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload = _valid_completed_session_payload()
    payload.analysis_artifact.schema_version = 2
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "schema_version must be 1" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_invalid_artifact_object_key_path_traversal() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload = _valid_completed_session_payload()
    payload.analysis_artifact.object_key = "../../etc/shadow"
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "invalid traversal" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_invalid_artifact_object_key_scope_mismatch() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    foreign_team_id = uuid4()
    payload = _valid_completed_session_payload()
    payload.analysis_artifact.object_key = f"teams/{foreign_team_id}/session_analysis.json"
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "scope mismatch" in str(exc_info.value) or "unauthorized resource" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_stale_attempt() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        attempt_number=1,
    )
    newer_attempt = AnalysisAttempt(
        id=uuid4(),
        session_id=job.practice_session_id,
        manifest_id=uuid4(),
        idempotency_key=None,
        attempt_number=2,
        status=AnalysisAttemptStatus.RUNNING,
        failure_code=None,
        failure_message=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )
    await uow.attempts.create(newer_attempt)

    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "is stale; current attempt is 2" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_cancelled_attempt_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.CANCELLED,
        last_update_sequence=1,
    )
    ancestry = await uow.jobs.get_ancestry_context(job.id)
    assert ancestry is not None
    cancelled_attempt = AnalysisAttempt(
        id=attempt.id,
        session_id=attempt.session_id,
        manifest_id=attempt.manifest_id,
        idempotency_key=None,
        attempt_number=attempt.attempt_number,
        status=AnalysisAttemptStatus.CANCELLED,
        failure_code=None,
        failure_message=None,
        created_at=attempt.created_at,
        started_at=attempt.started_at,
        completed_at=None,
        failed_at=None,
        cancelled_at=datetime.now(UTC),
        version=attempt.version,
    )
    updated_ancestry = AIJobAncestryContext(
        job=job,
        attempt=cancelled_attempt,
        session=ancestry.session,
        project_id=ancestry.project_id,
        team_id=ancestry.team_id,
    )
    assert isinstance(uow.jobs, FakeAnalysisJobRepository)
    uow.jobs.set_ancestry_context(job.id, updated_ancestry)

    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "Cannot complete cancelled" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_duplicate_sequence_no_side_effects() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=2,
    )
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.RUNNING
    assert result.last_update_sequence == 2
    assert uow.commit_count == 0
    assert uow.rollback_count == 0


@pytest.mark.asyncio
async def test_completed_duplicate_when_job_already_completed() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.COMPLETED,
        attempt_status=AnalysisAttemptStatus.COMPLETED,
        last_update_sequence=2,
    )
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2)

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.last_update_sequence == 2
    assert uow.commit_count == 0
    assert uow.rollback_count == 0


@pytest.mark.asyncio
async def test_completed_atomic_rollback_on_validation_failure() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        trace_id="trc_completely_wrong_trace",
    )

    with pytest.raises(CompletedResultValidationError):
        await service.record_update(job.id, update)

    assert job.status == AnalysisJobStatus.RUNNING
    assert job.last_update_sequence == 1
    assert job.completed_result is None
    assert attempt.status == AnalysisAttemptStatus.RUNNING
    assert attempt.completed_at is None
    assert uow.commit_count == 0
    assert uow.rollback_count >= 1


@pytest.mark.asyncio
async def test_completed_empty_evidence_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload_dict = _valid_completed_session_payload().model_dump(mode="json")
    payload_dict["primary_questions"][0]["evidence_ids"] = []
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload_dict)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "evidence_ids" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_question_count_under_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload_dict = _valid_completed_session_payload().model_dump(mode="json")
    payload_dict["primary_questions"] = payload_dict["primary_questions"][:2]
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload_dict)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "primary_questions" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_question_count_over_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload_dict = _valid_completed_session_payload().model_dump(mode="json")
    extra_q = dict(payload_dict["primary_questions"][0])
    extra_q["candidate_id"] = "cand_004"
    payload_dict["primary_questions"].append(extra_q)
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload_dict)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "primary_questions" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_duplicate_candidate_ids_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    payload_dict = _valid_completed_session_payload().model_dump(mode="json")
    payload_dict["primary_questions"][1]["candidate_id"] = payload_dict["primary_questions"][0][
        "candidate_id"
    ]
    update = _make_update(AIWorkerUpdateStatus.COMPLETED, sequence=2, payload=payload_dict)

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "candidate_id" in str(exc_info.value).lower()
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_valid_analyze_answer() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_type="analyze_answer",
    )
    payload = AnswerAnalysisCompletedPayload(
        answer_id="ans_001",
        transcript_artifact_id="01JEXAMPLE0000000000000061",
        assessment_artifact_id="01JEXAMPLE0000000000000062",
        follow_up=FollowUpQuestion(
            text="Can you elaborate on your churn assumptions?",
            reason="Clarify customer retention metrics",
            rubric_dimension="customer_retention",
            evidence_ids=["ev_answer_001"],
        ),
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_result is not None
    assert result.completed_result["answer_id"] == "ans_001"
    assert result.completed_result["follow_up"]["rubric_dimension"] == "customer_retention"
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_completed_valid_generate_report() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_type="generate_report",
    )
    payload = ReportCompletedPayload(
        evaluation_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000071",
            object_key="artifacts/evaluation.json",
            checksum="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            schema_version=1,
        ),
        report_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000072",
            object_key="artifacts/report.json",
            checksum="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            schema_version=1,
        ),
        member_feedback_user_ids=["user_001", "user_002"],
        limitations=[],
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_result is not None
    assert result.completed_result["member_feedback_user_ids"] == ["user_001", "user_002"]
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_completed_valid_erase_ai_data() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_type="erase_ai_data",
    )
    payload = ErasureCompletedPayload(
        erasure_request_id="era_001",
        deleted_records=14,
        deleted_objects=6,
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status == AnalysisJobStatus.COMPLETED
    assert result.completed_result is not None
    assert result.completed_result["deleted_records"] == 14
    assert result.completed_result["deleted_objects"] == 6
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_completed_negative_deleted_records_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
        job_type="erase_ai_data",
    )
    payload_dict = {
        "erasure_request_id": "era_001",
        "deleted_records": -1,
        "deleted_objects": 6,
    }
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload_dict,
    )

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "deleted_records" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0


@pytest.mark.asyncio
async def test_completed_unsupported_schema_version_rejected() -> None:
    job, attempt, uow, service = _build_attempt_and_job(
        job_status=AnalysisJobStatus.RUNNING,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        last_update_sequence=1,
    )
    update = AIWorkerUpdate.model_construct(
        schema_version=cast(Literal[1], 2),
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_update",
        payload=_valid_completed_session_payload(),
    )

    with pytest.raises(CompletedResultValidationError) as exc_info:
        await service.record_update(job.id, update)

    assert "Unsupported schema version" in str(exc_info.value)
    assert job.status == AnalysisJobStatus.RUNNING
    assert uow.commit_count == 0
