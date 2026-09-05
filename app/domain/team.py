# app/domain/entities/team.py

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from .project import Project
from .team_member import TeamMember


@dataclass
class Team:
    id: UUID
    name: str
    created_at: datetime
    version: int = 1
    members: list[TeamMember] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
