
from dataclasses import dataclass
from datetime import datetime
import enum
from uuid import UUID


class InvitationStatus(str ,enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DeliveryStatus(str ,enum.Enum):
    QUEUED = "queued"
    ACCEPTED = "accepted_by_gmail"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class TeamInvitation:
    id: UUID
    team_id: UUID
    email: str
    token_hash: str
    role: str
    status: InvitationStatus
    delivery_status: DeliveryStatus
    delivery_attempts: int
    created_at: datetime
    expires_at: datetime
    idempotency_key: str