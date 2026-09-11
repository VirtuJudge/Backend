from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class SpeakerMapping:
    id: UUID
    attempt_id: UUID
    speaker_label: str
    member_id: UUID | None
    mapped_by: UUID
    mapped_at: datetime