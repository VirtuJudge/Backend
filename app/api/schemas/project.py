from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    team_id: UUID
    name: str
    description: str | None
    created_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectResponse]
    next_cursor: str | None = None


class ProjectDeleteRequest(BaseModel):
    confirmation: str = Field(min_length=1)


class ErasureRequestResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str = "accepted"