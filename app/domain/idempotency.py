from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TeamCreationIdempotency:
    user_id: UUID
    key: str
    request_hash: str
    team_id: UUID
