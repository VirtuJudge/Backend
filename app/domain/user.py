from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    issuer: str
    subject: str
    email: str | None
    created_at: datetime