from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ErasureStatus = Literal["pending", "in_progress", "completed", "failed"]


class ErasureStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    store: str
    status: ErasureStatus
    attempts: int
    deleted_records: int
    deleted_objects: int
    failure_code: str | None
    completed_at: datetime | None


class ErasureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scope: Literal["asset", "practice_session", "project"]
    scope_id: UUID
    status: ErasureStatus
    requested_by: UUID | None
    requested_at: datetime
    deadline_at: datetime
    completed_at: datetime | None
    steps: list[ErasureStepResponse]
