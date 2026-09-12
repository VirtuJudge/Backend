from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TeamOwnerRequest(BaseModel):
    user_id: UUID


class TeamResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class TeamMembershipResponse(BaseModel):
    id: UUID
    team_id: UUID
    user_id: UUID
    role: str
    joined_at: datetime
    display_name: str | None = None


class TeamPage(BaseModel):
    items: list[TeamResponse]
    next_cursor: str | None = None


class TeamMembershipPage(BaseModel):
    items: list[TeamMembershipResponse]
    next_cursor: str | None = None
