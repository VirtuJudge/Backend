from typing import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.speaker_mapping import (
    SpeakerMapping,
)


class SpeakerMappingRepository(Protocol):
    async def get_by_id(
        self,
        mapping_id: UUID,
    ) -> SpeakerMapping | None: ...

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> list[SpeakerMapping]: ...

    async def get_by_speaker_label(
        self,
        attempt_id: UUID,
        speaker_label: str,
    ) -> SpeakerMapping | None: ...

    async def create(
        self,
        mapping: SpeakerMapping,
    ) -> SpeakerMapping: ...

    async def update(
        self,
        mapping: SpeakerMapping,
    ) -> SpeakerMapping: ...
