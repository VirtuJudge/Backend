from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


class ErasureNotFound(Exception):
    pass


class ErasureForbidden(Exception):
    pass


class ErasureConflict(Exception):
    pass


@dataclass(slots=True)
class ErasureStep:
    store: str
    status: str = "pending"
    attempts: int = 0
    deleted_records: int = 0
    deleted_objects: int = 0
    failure_code: str | None = None
    completed_at: datetime | None = None


@dataclass(slots=True)
class Erasure:
    id: UUID
    team_id: UUID
    scope: str
    scope_id: UUID
    requested_by: UUID | None
    requested_at: datetime
    deadline_at: datetime
    status: str = "pending"
    origin: str = "user_request"
    completed_at: datetime | None = None
    steps: list[ErasureStep] = field(default_factory=list)
    inventory: dict[str, Any] = field(default_factory=dict)
    lease_token: UUID | None = None


@dataclass(frozen=True, slots=True)
class ErasureJobState:
    id: UUID
    status: str
    last_update_sequence: int
    cancel_requested: bool = False
