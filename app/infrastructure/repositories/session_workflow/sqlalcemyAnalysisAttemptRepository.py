from uuid import UUID

from sqlalchemy import select, update ,func
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.analysis_attempt_repository import AnalysisAttemptRepository
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.configurations.session_workflow.analysisAttemptConfiguration import (
    AnalysisAttemptModel,
    AnalysisAttemptStatus,
)
from app.infrastructure.persistence.mappers.session_practice.analysis_attempt_mapper import (
    to_domain,
    to_model,
)
class SqlAlchemyAnalysisAttemptRepository(AnalysisAttemptRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisAttempt | None:

        stmt = select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.id == attempt_id
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_latest(
        self,
        session_id: UUID,
    ) -> AnalysisAttempt | None:

        stmt = (
            select(AnalysisAttemptModel)
            .where(AnalysisAttemptModel.session_id == session_id)
            .order_by(AnalysisAttemptModel.attempt_number.desc())
            .limit(1)
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_active(
        self,
        session_id: UUID,
    ) -> AnalysisAttempt | None:

        stmt = select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.session_id == session_id,
            AnalysisAttemptModel.status.in_(
                [
                    AnalysisAttemptStatus.PENDING,
                    AnalysisAttemptStatus.RUNNING,
                ]
            ),
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_by_number(
        self,
        session_id: UUID,
        attempt_number: int,
    ) -> AnalysisAttempt | None:

        stmt = select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.session_id == session_id,
            AnalysisAttemptModel.attempt_number == attempt_number,
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_next_attempt_number(
        self,
        session_id: UUID,
    ) -> int:

        stmt = select(
            func.coalesce(
                func.max(AnalysisAttemptModel.attempt_number),
                0,
            ) + 1
        ).where(
            AnalysisAttemptModel.session_id == session_id
        )

        result = await self._session.execute(stmt)

        return result.scalar_one()

    async def create(
        self,
        attempt: AnalysisAttempt,
    ) -> AnalysisAttempt:

        model = to_model(attempt)

        self._session.add(model)
        await self._session.flush()

        return to_domain(model)

    async def update(
        self,
        attempt: AnalysisAttempt,
        expected_version: int,
    ) -> AnalysisAttempt:

        stmt = (
            update(AnalysisAttemptModel)
            .where(
                AnalysisAttemptModel.id == attempt.id,
                AnalysisAttemptModel.version == expected_version,
            )
            .values(
                status=attempt.status,
                version=attempt.version,
                failure_code=attempt.failure_code,
                failure_message=attempt.failure_message,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                failed_at=attempt.failed_at,
                cancelled_at=attempt.cancelled_at,
            )
        )

        result = await self._session.execute(stmt)

        if result.rowcount != 1:
            raise StaleEntityVersion(
                "Analysis attempt was modified concurrently."
            )

        await self._session.flush()

        return attempt