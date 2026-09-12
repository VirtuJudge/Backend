from uuid import UUID

from typing import Protocol

from app.domain.session_workflow.entities.session_practice import PracticeSession


class PracticeSessionRepository(Protocol):
    async def get_by_id(
        self,
        session_id: UUID,
    ) -> PracticeSession | None: ...

    async def create(
        self,
        session: PracticeSession,
    ) -> PracticeSession: ...

    async def update(
        self,
        session: PracticeSession,
        expected_version: int,
    ) -> PracticeSession: ...

    async def exists(
        self,
        session_id: UUID,
    ) -> bool: ...

    async def list_by_project(
        self,
        project_id: UUID,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[PracticeSession], str | None]: ...
