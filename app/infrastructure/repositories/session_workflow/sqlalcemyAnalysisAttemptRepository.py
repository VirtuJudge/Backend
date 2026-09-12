from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.analysis_attempt_repository import (
    AnalysisAttemptRepository,
)
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.mappers.session_practice.analysis_attempt_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.analysisAttemptConfiguration import (
    AnalysisAttemptModel,
    AnalysisAttemptStatus,
)


class SqlAlchemyAnalysisAttemptRepository(AnalysisAttemptRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisAttempt | None:

        stmt = select(AnalysisAttemptModel).where(AnalysisAttemptModel.id == attempt_id)

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
            )
            + 1
        ).where(AnalysisAttemptModel.session_id == session_id)

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
                idempotency_key=attempt.idempotency_key,
            )
        )

        result = await self._session.execute(stmt)

        if result.rowcount != 1:
            raise StaleEntityVersion("Analysis attempt was modified concurrently.")

        await self._session.flush()

        return attempt

    async def get_by_idempotency_key(
        self,
        session_id: UUID,
        idempotency_key: str,
    ) -> AnalysisAttempt | None:
        stmt = select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.session_id == session_id,
            AnalysisAttemptModel.idempotency_key == idempotency_key,
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_all_by_session_id(
        self,
        session_id: UUID,
        next_cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[AnalysisAttempt], str | None]:
        stmt = (
            select(AnalysisAttemptModel)
            .where(
                AnalysisAttemptModel.session_id == session_id,
            )
            .order_by(
                AnalysisAttemptModel.id,
            )
            .limit(limit + 1)
        )
        if next_cursor is not None:
            cursor_id = UUID(next_cursor)
            stmt = stmt.where(AnalysisAttemptModel.id > cursor_id)
        result = await self._session.scalars(stmt)
        rows = result.all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].id) if has_more else None
        return [to_domain(row) for row in rows], next_cursor
