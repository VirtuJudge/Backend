from uuid import UUID

from sqlalchemy import select, update ,func
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.analysis_job_repository import AnalysisJobRepository
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.infrastructure.persistence.configurations.session_workflow.analysisJobConfiguration import (
    AnalysisJobModel,
)
from app.infrastructure.persistence.mappers.session_practice.analysis_job_mapper import (
    to_domain,
    to_model,
)
class SqlAlchemyAnalysisJobRepository(AnalysisJobRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:

        stmt = select(AnalysisJobModel).where(
            AnalysisJobModel.id == job_id
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisJob | None:

        stmt = select(AnalysisJobModel).where(
            AnalysisJobModel.attempt_id == attempt_id
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def create(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob:

        model = to_model(job)

        self._session.add(model)
        await self._session.flush()

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
        await self._session.flush()

        return job