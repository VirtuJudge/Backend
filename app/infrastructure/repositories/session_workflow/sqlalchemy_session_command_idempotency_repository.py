from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.session_command_idempotency_repository import (
    SessionCommandIdempotencyRepository,
)
from app.domain.session_workflow.entities.session_command_idempotency import (
    SessionCommandIdempotency,
)
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.persistence.configurations import (
    SessionCommandIdempotencyModel,
)


class SqlAlchemySessionCommandIdempotencyRepository(SessionCommandIdempotencyRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        session_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> SessionCommandIdempotency | None:
        stmt = select(SessionCommandIdempotencyModel).where(
            SessionCommandIdempotencyModel.session_id == session_id,
            SessionCommandIdempotencyModel.actor_id == actor_id,
            SessionCommandIdempotencyModel.operation == operation,
            SessionCommandIdempotencyModel.idempotency_key == idempotency_key,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return SessionCommandIdempotency(
            id=row.id,
            session_id=row.session_id,
            actor_id=row.actor_id,
            operation=row.operation,
            idempotency_key=row.idempotency_key,
            request_hash=row.request_hash,
            created_at=row.created_at,
        )

    async def create(
        self,
        record: SessionCommandIdempotency,
    ) -> SessionCommandIdempotency:
        model = SessionCommandIdempotencyModel(
            id=record.id,
            session_id=record.session_id,
            actor_id=record.actor_id,
            operation=record.operation,
            idempotency_key=record.idempotency_key,
            request_hash=record.request_hash,
            created_at=record.created_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Idempotency record already exists.") from exc
        return record
