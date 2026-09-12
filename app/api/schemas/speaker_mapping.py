from uuid import UUID

from pydantic import BaseModel, Field


class SpeakerMappingRequest(BaseModel):
    speaker_label: str = Field(min_length=1, max_length=100)
    user_id: UUID


class UpdateSpeakerMappingsRequest(BaseModel):
    mappings: list[SpeakerMappingRequest]
