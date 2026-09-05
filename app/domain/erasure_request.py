from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ErasureRequest:
    id: UUID
    project_id: UUID
    requested_by: UUID
    created_at: datetime
