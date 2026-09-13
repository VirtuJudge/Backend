from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.persistence.mappers.session_practice.analysis_job_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.analysis_job_configuration import (
    AnalysisJobModel,
)


class SqlAlchemyAnalysisJobRepository(AnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:

        stmt = select(AnalysisJobModel).where(AnalysisJobModel.id == job_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisJob | None:

        stmt = select(AnalysisJobModel).where(AnalysisJobModel.attempt_id == attempt_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def create(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob:
        model = to_model(job)
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Analysis job already exists or conflict.") from exc
        return to_domain(model)

    async def update(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob:
        stmt = (
            update(AnalysisJobModel)
            .where(AnalysisJobModel.id == job.id)
            .values(
                status=job.status,
                retry_count=job.retry_count,
                last_error=job.last_error,
                started_at=job.started_at,
                completed_at=job.completed_at,
            )
        )
        await self._session.execute(stmt)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Integrity conflict on analysis job update.") from exc
        return job
