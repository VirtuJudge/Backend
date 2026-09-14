import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    ArtifactRef,
    PrimaryQuestion,
    ProgressPayload,
    SessionAnalysisCompletedPayload,
)
from app.application.ai_jobs import AIJobs
from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.entities.session_command_idempotency import (
    SessionCommandIdempotency,
)
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import InvalidJobStatusTransition
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fake_analysis_attempt_repository import FakeAnalysisAttemptRepository
from tests.support.fake_analysis_job_repository import FakeAnalysisJobRepository


class FakePracticeSessionRepository:
    def __init__(self, initial_sessions: list[PracticeSession] | None = None) -> None:
        self.sessions: dict[UUID, PracticeSession] = {s.id: s for s in (initial_sessions or [])}

    async def get_by_id(self, session_id: UUID) -> PracticeSession | None:
        return self.sessions.get(session_id)

    async def create(self, session: PracticeSession) -> PracticeSession:
        self.sessions[session.id] = session
        return session

    async def update(self, session: PracticeSession, expected_version: int) -> PracticeSession:
        self.sessions[session.id] = session
        return session

    async def list_by_project(
        self,
        project_id: UUID,
        cursor: str | None = None,
        limit: int = 20,
    ) -> list[PracticeSession]:
        return [s for s in self.sessions.values() if s.project_id == project_id]


class FakeProjectRepository:
    def __init__(self) -> None:
        self.members: set[tuple[UUID, UUID]] = set()
        self.owners: set[tuple[UUID, UUID]] = set()

    async def is_member(self, project_id: UUID, user_id: UUID) -> bool:
        return True

    async def is_owner(self, project_id: UUID, user_id: UUID) -> bool:
        return True

    async def get_by_id(self, project_id: UUID) -> Any:
        return object()


class FakeSessionManifestRepository:
    def __init__(self) -> None:
        self.manifests: dict[UUID, SessionManifest] = {}

    async def get_by_session_id(self, session_id: UUID) -> SessionManifest | None:
        return self.manifests.get(session_id)

    async def create(self, manifest: SessionManifest) -> SessionManifest:
        self.manifests[manifest.session_id] = manifest
        return manifest


class FakeIdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, UUID, str, str], SessionCommandIdempotency] = {}

    async def get(
        self,
        session_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> SessionCommandIdempotency | None:
        return self.records.get((session_id, actor_id, operation, idempotency_key))

    async def create(
        self,
        record: SessionCommandIdempotency,
    ) -> SessionCommandIdempotency:
        key = (record.session_id, record.actor_id, record.operation, record.idempotency_key)
        self.records[key] = record
        return record


class MemoryUnitOfWork:
    sessions: Any
    attempts: Any
    jobs: Any
    manifests: Any
    projects: Any
    speaker_mappings: Any
    idempotency: Any

    def __init__(
        self,
        sessions: Any,
        attempts: Any,
        jobs: Any,
        manifests: Any,
        projects: Any,
        idempotency: Any,
    ) -> None:
        self.sessions = sessions
        self.attempts = attempts
        self.jobs = jobs
        self.manifests = manifests
        self.projects = projects
        self.speaker_mappings = None
        self.idempotency = idempotency
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def __aenter__(self) -> "MemoryUnitOfWork":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            await self.rollback()


def _valid_completed_payload() -> SessionAnalysisCompletedPayload:
    return SessionAnalysisCompletedPayload(
        analysis_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000051",
            object_key="artifacts/session/analysis.json",
            checksum="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            schema_version=1,
        ),
        primary_questions=[
            PrimaryQuestion(
                candidate_id="cand_001",
                text="What are your assumptions on customer acquisition costs?",
                reason="The presentation states CAC without justification.",
                rubric_dimension="business_reasoning",
                evidence_ids=["ev_slide_03"],
            ),
            PrimaryQuestion(
                candidate_id="cand_002",
                text="How do you handle data sync latency?",
                reason="Architecture diagram shows distributed db without sync mechanism.",
                rubric_dimension="technical_feasibility",
                evidence_ids=["ev_speech_01"],
            ),
            PrimaryQuestion(
                candidate_id="cand_003",
                text="What is your mitigation plan for regulatory delays?",
                reason="Timeline has no buffer for compliance milestones.",
                rubric_dimension="technical_feasibility",
                evidence_ids=["ev_slide_09"],
            ),
        ],
        speaker_labels=["SPEAKER_00", "SPEAKER_01"],
        limitations=[],
    )


def _setup_environment() -> tuple[
    MemoryUnitOfWork,
    PracticeSession,
    SessionManifest,
    AnalysisAttempt,
    AnalysisJob,
    SessionWorkflow,
    AIJobs,
    UUID,
]:
    now = datetime.now(UTC)
    actor_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    attempt_id = uuid4()
    job_id = uuid4()

    session = PracticeSession(
        id=session_id,
        project_id=project_id,
        created_by=actor_id,
        name="Pitch Session",
        status=SessionStatus.ANALYZING,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
        started_at=now,
        completed_at=None,
        cancelled_at=None,
    )
    manifest = SessionManifest(
        id=manifest_id,
        session_id=session_id,
        presentation_version_id=uuid4(),
        supporting_document_version_ids=[],
        rubric_id="startup_pitch",
        frozen_at=now,
    )
    attempt = AnalysisAttempt(
        id=attempt_id,
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key="attempt-1",
        attempt_number=1,
        status=AnalysisAttemptStatus.RUNNING,
        failure_code=None,
        failure_message=None,
        created_at=now,
        started_at=now,
        completed_at=None,
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
        status=AnalysisJobStatus.RUNNING,
        correlation_id=uuid4(),
        last_update_sequence=1,
        payload_version=1,
        attempts=1,
        cancel_requested=False,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=None,
        payload={"trace_id": "trc_test_race"},
        queued_at=now,
        next_dispatch_at=None,
        dispatch_retry_count=0,
        last_dispatch_error_category=None,
        completed_result=None,
    )

    sessions_repo = FakePracticeSessionRepository([session])
    attempts_repo = FakeAnalysisAttemptRepository([attempt])
    jobs_repo = FakeAnalysisJobRepository([job])
    manifests_repo = FakeSessionManifestRepository()
    manifests_repo.manifests[session_id] = manifest
    projects_repo = FakeProjectRepository()
    idempotency_repo = FakeIdempotencyRepository()

    uow = MemoryUnitOfWork(
        sessions=sessions_repo,
        attempts=attempts_repo,
        jobs=jobs_repo,
        manifests=manifests_repo,
        projects=projects_repo,
        idempotency=idempotency_repo,
    )

    ancestry = AIJobAncestryContext(
        job=job,
        attempt=attempt,
        session=session,
        project_id=project_id,
        team_id=uuid4(),
    )
    jobs_repo.set_ancestry_context(job.id, ancestry)

    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)
    workflow = SessionWorkflow(uow, ai_jobs=ai_jobs, queue=queue)

    return uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id


@pytest.mark.asyncio
async def test_cancelling_active_session_transactionally_sets_cancel_requested_on_job() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    cancelled_session = await workflow.cancel(
        session_id=session.id,
        actor_id=actor_id,
        reason="User requested cancellation",
        idempotency_key="cancel-key-1",
    )

    assert cancelled_session.status == SessionStatus.CANCELLED
    assert cancelled_session.cancelled_at is not None
    assert attempt.status == AnalysisAttemptStatus.CANCELLED
    assert attempt.cancelled_at is not None
    assert job.cancel_requested is True
    assert job.status == AnalysisJobStatus.CANCELLED
    assert job.completed_at is not None
    assert uow.commit_count == 1


@pytest.mark.asyncio
async def test_cancellation_remains_idempotent() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    first = await workflow.cancel(
        session_id=session.id,
        actor_id=actor_id,
        reason="Cancel reason",
        idempotency_key="cancel-idemp-1",
    )
    assert first.status == SessionStatus.CANCELLED
    commits_after_first = uow.commit_count

    second = await workflow.cancel(
        session_id=session.id,
        actor_id=actor_id,
        reason="Cancel reason",
        idempotency_key="cancel-idemp-1",
    )
    assert second.status == SessionStatus.CANCELLED
    assert uow.commit_count == commits_after_first


@pytest.mark.asyncio
async def test_progress_arriving_after_cancellation_acknowledged_but_ignored() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    await workflow.cancel(
        session_id=session.id,
        actor_id=actor_id,
        reason="Cancel session",
    )
    assert job.cancel_requested is True
    assert job.status == AnalysisJobStatus.CANCELLED

    update = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.PROGRESS,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload=ProgressPayload(stage="speech", progress=0.75, message="Transcribing"),
    )

    result = await ai_jobs.record_update(job.id, update)
    assert result is not None
    assert result.status == AnalysisJobStatus.CANCELLED
    assert result.cancel_requested is True
    assert result.last_update_sequence == 2
    assert "progress" not in (result.payload or {})
    assert attempt.status == AnalysisAttemptStatus.CANCELLED


@pytest.mark.asyncio
async def test_completion_arriving_after_cancellation_acknowledged_but_ignored() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    await workflow.cancel(
        session_id=session.id,
        actor_id=actor_id,
        reason="Cancel session",
    )

    update = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload=_valid_completed_payload(),
    )

    result = await ai_jobs.record_update(job.id, update)
    assert result is not None
    assert result.status == AnalysisJobStatus.CANCELLED
    assert result.cancel_requested is True
    assert result.last_update_sequence == 2
    assert result.completed_result is None
    assert attempt.status == AnalysisAttemptStatus.CANCELLED
    assert session.status == SessionStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_versus_callback_race_concurrent() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    update = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload=_valid_completed_payload(),
    )

    async def run_cancel() -> PracticeSession:
        return await workflow.cancel(
            session_id=session.id,
            actor_id=actor_id,
            reason="Concurrent cancel",
        )

    async def run_update() -> AnalysisJob | None:
        return await ai_jobs.record_update(job.id, update)

    await asyncio.gather(run_cancel(), run_update())

    assert session.status == SessionStatus.CANCELLED
    assert job.cancel_requested is True
    assert job.status == AnalysisJobStatus.CANCELLED
    assert attempt.status == AnalysisAttemptStatus.CANCELLED


@pytest.mark.asyncio
async def test_retry_creates_new_attempt_and_job_without_overwriting_earlier() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    session.status = SessionStatus.FAILED
    attempt.status = AnalysisAttemptStatus.FAILED
    attempt.failed_at = datetime.now(UTC)
    job.status = AnalysisJobStatus.FAILED
    job.completed_at = datetime.now(UTC)

    new_attempt = await workflow.retry(
        session_id=session.id,
        actor_id=actor_id,
        idempotency_key="retry-1",
    )

    assert new_attempt.attempt_number == 2
    assert new_attempt.status == AnalysisAttemptStatus.QUEUED
    assert session.status == SessionStatus.ANALYZING

    attempt_1 = await uow.attempts.get_by_id(attempt.id)
    assert attempt_1 is not None
    assert attempt_1.attempt_number == 1
    assert attempt_1.status == AnalysisAttemptStatus.FAILED

    new_job = await uow.jobs.get_by_attempt_id(new_attempt.id)
    assert new_job is not None
    assert new_job.analysis_attempt == 2
    assert new_job.status in {AnalysisJobStatus.PENDING, AnalysisJobStatus.QUEUED}

    old_job = await uow.jobs.get_by_id(job.id)
    assert old_job is not None
    assert old_job.analysis_attempt == 1
    assert old_job.status == AnalysisJobStatus.FAILED


@pytest.mark.asyncio
async def test_stale_completion_acknowledged_without_changing_current_session_or_attempt() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    session.status = SessionStatus.FAILED
    attempt.status = AnalysisAttemptStatus.FAILED
    attempt.failed_at = datetime.now(UTC)
    job.status = AnalysisJobStatus.RUNNING

    new_attempt = await workflow.retry(
        session_id=session.id,
        actor_id=actor_id,
        idempotency_key="retry-1",
    )
    assert session.status == SessionStatus.ANALYZING
    assert new_attempt.attempt_number == 2

    stale_update = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload=_valid_completed_payload(),
    )

    stale_result = await ai_jobs.record_update(job.id, stale_update)
    assert stale_result is not None
    assert stale_result.last_update_sequence == 2

    assert session.status == SessionStatus.ANALYZING
    current_attempt = await uow.attempts.get_latest(session.id)
    assert current_attempt is not None
    assert current_attempt.id == new_attempt.id
    assert current_attempt.attempt_number == 2
    assert current_attempt.status == AnalysisAttemptStatus.QUEUED
    assert current_attempt.completed_at is None


@pytest.mark.asyncio
async def test_retry_versus_stale_completion_race_concurrent() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    session.status = SessionStatus.FAILED
    attempt.status = AnalysisAttemptStatus.FAILED
    attempt.failed_at = datetime.now(UTC)
    job.status = AnalysisJobStatus.RUNNING

    stale_update = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.COMPLETED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload=_valid_completed_payload(),
    )

    async def run_retry() -> AnalysisAttempt:
        return await workflow.retry(
            session_id=session.id,
            actor_id=actor_id,
            idempotency_key="retry-race-1",
        )

    async def run_stale_update() -> AnalysisJob | None:
        return await ai_jobs.record_update(job.id, stale_update)

    await asyncio.gather(run_retry(), run_stale_update())

    assert session.status == SessionStatus.ANALYZING
    current_attempt = await uow.attempts.get_latest(session.id)
    assert current_attempt is not None
    assert current_attempt.attempt_number == 2
    assert current_attempt.status == AnalysisAttemptStatus.QUEUED


@pytest.mark.asyncio
async def test_terminal_jobs_cannot_be_revived() -> None:
    uow, session, manifest, attempt, job, workflow, ai_jobs, actor_id = _setup_environment()

    job.status = AnalysisJobStatus.COMPLETED
    job.last_update_sequence = 1
    attempt.status = AnalysisAttemptStatus.COMPLETED

    update_started = AIWorkerUpdate(
        schema_version=1,
        sequence=2,
        status=AIWorkerUpdateStatus.STARTED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_test_race",
        payload={"pipeline_version": "1.0"},
    )
    with pytest.raises(InvalidJobStatusTransition):
        await ai_jobs.record_update(job.id, update_started)

    job.status = AnalysisJobStatus.CANCELLED
    job.cancel_requested = True
    attempt.status = AnalysisAttemptStatus.CANCELLED

    with pytest.raises(InvalidJobStatusTransition):
        await ai_jobs.record_update(job.id, update_started)
