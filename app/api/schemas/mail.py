from pydantic import BaseModel, EmailStr
from uuid import UUID

from datetime import datetime
import enum

from app.domain.team_invitation import InvitationStatus, DeliveryStatus

    
class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str


class InviteMemberResponse(BaseModel):
    id: UUID
    team_id: UUID
    email: EmailStr
    role: str
    status: InvitationStatus
    delivery_status: DeliveryStatus
    delivery_attempts: int
    created_at: datetime
    expires_at: datetime

class InvitationPageResponse(BaseModel):
    items: list[InviteMemberResponse]
    next_cursor: str | None = None
