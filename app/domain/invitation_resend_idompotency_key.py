from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class InvitationResendIdempotency:
    id: UUID
    actor_id: UUID
    team_id: UUID
    operation: str
    request_hash: str
    invitation_id: UUID
    key: str
    created_at: datetime
