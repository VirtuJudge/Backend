from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from app.application.ports.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from tests.support.fake_analysis_attempt_repository import FakeAnalysisAttemptRepository

_DEFAULT_ATTEMPTS = object()


class FakeAnalysisJobRepository(AnalysisJobRepository):
    def __init__(
        self,
        initial_jobs: list[AnalysisJob] | None = None,
        ancestry_contexts: dict[UUID, AIJobAncestryContext | None] | None = None,
        auto_ancestry: bool = True,
    ) -> None:
        self.jobs: dict[UUID, AnalysisJob] = {j.id: j for j in (initial_jobs or [])}
        self.ancestry_contexts: dict[UUID, AIJobAncestryContext | None] = (
            dict(ancestry_contexts) if ancestry_contexts else {}
        )
        self.auto_ancestry = auto_ancestry

    async def get_by_id(self, job_id: UUID) -> AnalysisJob | None:
        return self.jobs.get(job_id)

    async def get_by_id_for_update(self, job_id: UUID) -> AnalysisJob | None:
        return self.jobs.get(job_id)

    async def get_by_attempt_id(self, attempt_id: UUID) -> AnalysisJob | None:
        for job in self.jobs.values():
            if job.attempt_id == attempt_id:
                return job
        return None

    async def get_by_answer_id(self, answer_id: UUID) -> AnalysisJob | None:
        for job in self.jobs.values():
            if job.answer_id == answer_id:
                return job
        return None

    async def create(self, job: AnalysisJob) -> AnalysisJob:
        self.jobs[job.id] = job
        return job

    async def update(self, job: AnalysisJob) -> AnalysisJob:
        self.jobs[job.id] = job
        return job

    async def get_eligible_pending_jobs(
        self,
        now: datetime,
        limit: int = 10,
        *,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[AnalysisJob]:
        now_cmp = now.replace(tzinfo=UTC) if now.tzinfo is None else now
        eligible: list[AnalysisJob] = []
        for job in self.jobs.values():
            if job.status == AnalysisJobStatus.PENDING and not job.cancel_requested:
                job_next = (
                    job.next_dispatch_at.replace(tzinfo=UTC)
                    if (job.next_dispatch_at is not None and job.next_dispatch_at.tzinfo is None)
                    else job.next_dispatch_at
                )
                if job_next is None or job_next <= now_cmp:
                    eligible.append(job)

        def _sort_key(j: AnalysisJob) -> tuple[Any, ...]:
            next_at = j.next_dispatch_at
            if next_at is not None and next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=UTC)
            created_at = j.created_at
            if created_at is not None and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return (
                next_at is not None,
                next_at or datetime.min.replace(tzinfo=UTC),
                created_at or datetime.min.replace(tzinfo=UTC),
                str(j.id),
            )

        eligible.sort(key=_sort_key)
        return eligible[:limit]

    async def load_eligible_pending_batch(
        self,
        now: datetime,
        limit: int = 10,
        *,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[AnalysisJob]:
        return await self.get_eligible_pending_jobs(
            now, limit=limit, for_update=for_update, skip_locked=skip_locked
        )

    async def change_pending_to_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> AnalysisJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status != AnalysisJobStatus.PENDING or job.cancel_requested:
            return None
        job.status = AnalysisJobStatus.QUEUED
        job.queued_at = now
        job.updated_at = now
        if payload is not None:
            job.payload = payload
        return job

    async def mark_as_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        return await self.change_pending_to_queued(job_id, now, payload=payload) is not None

    async def record_dispatch_failure(
        self,
        job_id: UUID,
        now: datetime,
        next_eligible_at: datetime,
        error_category: str,
        error_message: str | None = None,
    ) -> AnalysisJob | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.status = AnalysisJobStatus.PENDING
        job.dispatch_retry_count += 1
        job.next_dispatch_at = next_eligible_at
        job.last_dispatch_error_category = error_category
        job.last_error = error_message
        job.updated_at = now
        return job

    async def apply_callback(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> bool:
        job = await self.apply_callback_update(
            job_id,
            sequence,
            status,
            occurred_at,
            now=now,
            payload=payload,
            error_message=error_message,
            attempts=attempts,
        )
        return job is not None

    async def apply_callback_update(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> AnalysisJob | None:
        job = self.jobs.get(job_id)
        if job is None or sequence <= job.last_update_sequence:
            return None
        job.last_update_sequence = sequence
        if isinstance(status, AnalysisJobStatus):
            job.status = status
        else:
            try:
                job.status = AnalysisJobStatus(status)
            except ValueError:
                job.status = AnalysisJobStatus.RUNNING
        job.updated_at = now or occurred_at
        if error_message is not None:
            job.last_error = error_message
        if attempts is not None:
            job.attempts = attempts
        return job

    def set_ancestry_context(self, job_id: UUID, context: AIJobAncestryContext | None) -> None:
        self.ancestry_contexts[job_id] = context

    async def get_ancestry_context(self, job_id: UUID) -> AIJobAncestryContext | None:
        if job_id in self.ancestry_contexts:
            return self.ancestry_contexts[job_id]
        if not self.auto_ancestry:
            return None
        job = self.jobs.get(job_id)
        if job is None:
            return None
        project_id = uuid4()
        team_id = uuid4()
        attempt = AnalysisAttempt(
            id=job.attempt_id,
            session_id=job.practice_session_id,
            manifest_id=uuid4(),
            idempotency_key=None,
            attempt_number=job.analysis_attempt,
            status=AnalysisAttemptStatus.RUNNING,
            failure_code=None,
            failure_message=None,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=None,
            failed_at=None,
            cancelled_at=None,
            version=1,
        )
        session = PracticeSession(
            id=job.practice_session_id,
            project_id=project_id,
            created_by=uuid4(),
            name="Test Session",
            status=SessionStatus.ANALYZING,
            version=1,
            created_at=job.created_at,
            updated_at=job.updated_at,
            consent_granted=True,
            started_at=job.started_at,
            completed_at=None,
            cancelled_at=None,
        )
        context = AIJobAncestryContext(
            job=job,
            attempt=attempt,
            session=session,
            project_id=project_id,
            team_id=team_id,
        )
        self.ancestry_contexts[job_id] = context
        return context

    async def load_ancestry_context(self, job_id: UUID) -> AIJobAncestryContext | None:
        return await self.get_ancestry_context(job_id)

    async def get_ancestry_context_by_attempt_id(
        self, attempt_id: UUID
    ) -> AIJobAncestryContext | None:
        for ctx in self.ancestry_contexts.values():
            if ctx is not None and ctx.attempt_id == attempt_id:
                return ctx
        return None


class FakeUnitOfWork:
    sessions: Any
    manifests: Any
    attempts: Any
    projects: Any
    jobs: AnalysisJobRepository
    speaker_mappings: Any
    idempotency: Any

    def __init__(
        self,
        jobs: AnalysisJobRepository | None = None,
        attempts: Any = _DEFAULT_ATTEMPTS,
    ) -> None:
        self.jobs = jobs or FakeAnalysisJobRepository()
        self.sessions = None
        self.manifests = None
        self.attempts = (
            FakeAnalysisAttemptRepository() if attempts is _DEFAULT_ATTEMPTS else attempts
        )
        self.projects = None
        self.speaker_mappings = None
        self.idempotency = None
        self.qa = MagicMock()
        self.qa.get_round_by_session = AsyncMock(return_value=None)
        self.qa.create_round = AsyncMock()
        self.qa.create_questions = AsyncMock()
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
