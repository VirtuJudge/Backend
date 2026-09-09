import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency


class InvitationStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DeliveryStatus(enum.StrEnum):
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
    resend_idempotency_keys: list["InvitationResendIdempotency"]
    version: int
