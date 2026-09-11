## infrastructure/persistence/repositories/practice_session_repository.py

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.application.interfaces.session_practice.session_practice_repository import (
    PracticeSessionRepository,
)
from app.infrastructure.persistence.configurations.session_workflow.sessionPracticeConfiguration import (
    PracticeSessionModel,
)
from app.infrastructure.persistence.mappers.session_practice.session_practice_mapper import (
    to_domain,
    to_model,
)

from app.domain.session_workflow.exceptions import (
    StaleEntityVersion,
)


class SqlAlchemyPracticeSessionRepository(PracticeSessionRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        session_id: UUID,
    ) -> PracticeSession | None:

        stmt = select(PracticeSessionModel).where(
            PracticeSessionModel.id == session_id
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            return None

        return to_domain(model)

    async def exists(
        self,
        session_id: UUID,
    ) -> bool:

        stmt = select(PracticeSessionModel.id).where(
            PracticeSessionModel.id == session_id
        )

        result = await self._session.execute(stmt)

        return result.scalar_one_or_none() is not None

    async def create(
        self,
        session: PracticeSession,
    ) -> PracticeSession:

        model = to_model(session)

        self._session.add(model)

        await self._session.flush()

        return to_domain(model)

    async def update(
        self,
        session: PracticeSession,
        expected_version: int,
    ) -> PracticeSession:

        stmt = (
            update(PracticeSessionModel)
            .where(
                PracticeSessionModel.id == session.id,
                PracticeSessionModel.version == expected_version,
            )
            .values(
                status=session.status,
                version=session.version,
                updated_at=session.updated_at,
                started_at=session.started_at,
                completed_at=session.completed_at,
                cancelled_at=session.cancelled_at,
            )
        )

        result = await self._session.execute(stmt)

        if result.rowcount != 1:
            raise StaleEntityVersion(
                "Practice session was modified by another request."
            )

        await self._session.flush()

        return session

    async def exists(
        self,
        session_id: UUID,
    ) -> bool:

        stmt = select(PracticeSessionModel.id).where(
            PracticeSessionModel.id == session_id
        )

        result = await self._session.execute(stmt)

        return result.scalar_one_or_none() is not None