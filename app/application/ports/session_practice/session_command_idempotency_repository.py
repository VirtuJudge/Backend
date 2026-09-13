from typing import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.session_command_idempotency import (
    SessionCommandIdempotency,
)


class SessionCommandIdempotencyRepository(Protocol):
    async def get(
        self,
        session_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> SessionCommandIdempotency | None: ...

    async def create(
        self,
        record: SessionCommandIdempotency,
    ) -> SessionCommandIdempotency: ...
