import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, model_validator

from app.domain.team_invitation import DeliveryStatus, InvitationStatus


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: Literal["member"] = "member"


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
    version: int = 1
    etag: str | None = None

    @model_validator(mode="after")
    def compute_etag(self) -> "InviteMemberResponse":
        if not self.etag and self.id and self.version is not None:
            self.etag = hashlib.sha256(f"{self.id}:{self.version}".encode()).hexdigest()
        return self


class InvitationPageResponse(BaseModel):
    items: list[InviteMemberResponse]
    next_cursor: str | None = None


class InvitationPreview(BaseModel):
    team_name: str
    invited_email: str
    role: str
    invited_by_name: str
    expires_at: datetime
    status: InvitationStatus
