from datetime import datetime
from uuid import UUID, uuid4

from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus


class AIJobs:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def create_pending_job(
        self,
        practice_session_id: UUID,
        attempt: AnalysisAttempt,
        now: datetime,
    ) -> AnalysisJob:
        job = AnalysisJob(
            id=uuid4(),
            practice_session_id=practice_session_id,
            attempt_id=attempt.id,
            analysis_attempt=attempt.attempt_number,
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
        await self._uow.jobs.create(job)
        return job

    async def request_cancellation(
        self,
        attempt_id: UUID,
        now: datetime,
    ) -> AnalysisJob | None:
        job = await self._uow.jobs.get_by_attempt_id(attempt_id)
        if job is None:
            return None
        job.cancel_requested = True
        job.updated_at = now
        if job.status not in {
            AnalysisJobStatus.COMPLETED,
            AnalysisJobStatus.FAILED,
            AnalysisJobStatus.CANCELLED,
        }:
            job.status = AnalysisJobStatus.CANCELLED
            job.completed_at = now
        await self._uow.jobs.update(job)
        return job
