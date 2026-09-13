from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class SessionCommandIdempotency:
    id: UUID
    session_id: UUID
    actor_id: UUID
    operation: str
    idempotency_key: str
    request_hash: str
    created_at: datetime
