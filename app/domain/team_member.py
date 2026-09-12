# app/domain/entities/team_member.py

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class TeamMember:
    id: UUID
    team_id: UUID
    user_id: UUID
    role: str
    joined_at: datetime
    display_name: str | None = None
