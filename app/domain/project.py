# app/domain/entities/project.py

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ProjectNotFoundError(Exception):
    pass


@dataclass
class Project:
    id: UUID
    team_id: UUID
    name: str
    description: str | None
    created_at: datetime
    version: int = 1
