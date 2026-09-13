## infrastructure/persistence/repositories/practice_session_repository.py

from uuid import UUID
from typing import Any

from sqlalchemy import select, update,cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.session_practice_repository import (
    PracticeSessionRepository,
)
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.exceptions import (
    StaleEntityVersion,
)
from app.infrastructure.persistence.mappers.session_practice.session_practice_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.sessionPracticeConfiguration import (
    PracticeSessionModel,
)


class SqlAlchemyPracticeSessionRepository(PracticeSessionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        session_id: UUID,
    ) -> PracticeSession | None:

        stmt = select(PracticeSessionModel).where(PracticeSessionModel.id == session_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            return None

        return to_domain(model)

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
                version=session.version + 1,
                updated_at=session.updated_at,
                started_at=session.started_at,
                completed_at=session.completed_at,
                cancelled_at=session.cancelled_at,
                consent_granted=session.consent_granted,
            )
        )

        result = cast(
            CursorResult[Any],
            await self._session.execute(stmt),
        )

        if result.rowcount != 1:
            raise StaleEntityVersion("Practice session was modified by another request.")

        await self._session.flush()

        session.version += 1

        return session

    async def exists(
        self,
        session_id: UUID,
    ) -> bool:

        stmt = select(PracticeSessionModel.id).where(PracticeSessionModel.id == session_id)

        result = await self._session.execute(stmt)

        return result.scalar_one_or_none() is not None

    async def list_by_project(
        self,
        project_id: UUID,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[PracticeSession], str | None]:

        stmt = (
            select(PracticeSessionModel)
            .where(PracticeSessionModel.project_id == project_id)
            .order_by(PracticeSessionModel.id)
            .limit(limit + 1)
        )
        if cursor:
            cursor_id = UUID(cursor)
            stmt = stmt.where(PracticeSessionModel.id > cursor_id)
        if search:
            stmt = stmt.where(PracticeSessionModel.name.ilike(f"%{search}%"))
        result = await self._session.scalars(stmt)
        rows = result.all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].id) if has_more else None

        return [to_domain(row) for row in rows], next_cursor
