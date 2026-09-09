from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.domain.team_invitation import DeliveryStatus, InvitationStatus


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


class InvitationPreview(BaseModel):
    team_name: str
    invited_email: EmailStr
    role: str
    invited_by_name: str
    expires_at: datetime
