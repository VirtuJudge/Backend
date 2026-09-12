from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.erasure_request import ErasureRequest
from app.domain.project import Project


class ProjectRepository(ABC):
    @abstractmethod
    async def list_for_team(
        self,
        team_id: UUID,
        cursor: UUID | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Project], UUID | None]: ...

    @abstractmethod
    async def get_by_id(self, project_id: UUID) -> Project | None: ...

    @abstractmethod
    async def create(self, project: Project) -> Project: ...

    @abstractmethod
    async def update(
        self,
        project_id: UUID,
        name: str | None,
        description: str | None,
        description_provided: bool,
        expected_version: int,
    ) -> Project | None: ...

    @abstractmethod
    async def get_erasure_request(
        self,
        project_id: UUID,
        requested_by: UUID,
        key: str,
    ) -> ErasureRequest | None: ...

    @abstractmethod
    async def create_erasure_request(self, request: ErasureRequest, key: str) -> ErasureRequest: ...

    @abstractmethod
    async def is_member(self, project_id: UUID, user_id: UUID) -> bool: ...
