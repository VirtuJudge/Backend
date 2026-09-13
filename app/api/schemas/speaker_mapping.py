from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SpeakerMappingRequest(BaseModel):
    speaker_label: str = Field(min_length=1, max_length=100)
    user_id: UUID


class UpdateSpeakerMappingsRequest(BaseModel):
    mappings: list[SpeakerMappingRequest]


class SpeakerMappingResponse(BaseModel):
    id: UUID
    attempt_id: UUID
    speaker_label: str
    user_id: UUID | None = None
    member_id: UUID | None = None
    mapped_by: UUID
    mapped_at: datetime

    model_config = ConfigDict(from_attributes=True)
