from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class InvitationResendIdempotency:
    id: UUID
    invitation_id: UUID
    key: str
    created_at: datetime