from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.idempotency import TeamCreationIdempotency
from app.domain.team import Team
from app.domain.team_member import TeamMember


class TeamRepository(ABC):
    @abstractmethod
    async def list_for_user(
        self,
        user_id: UUID,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Team], UUID | None]: ...

    @abstractmethod
    async def get_by_id(self, team_id: UUID) -> Team | None: ...

    @abstractmethod
    async def get_by_name(self, name: str) -> Team | None: ...

    @abstractmethod
    async def create(self, team: Team, owner: TeamMember) -> Team: ...

    @abstractmethod
    async def get_creation_idempotency(
        self,
        user_id: UUID,
        key: str,
    ) -> TeamCreationIdempotency | None: ...

    @abstractmethod
    async def save_creation_idempotency(
        self,
        record: TeamCreationIdempotency,
    ) -> None: ...

    @abstractmethod
    async def update_name(
        self,
        team_id: UUID,
        name: str,
        expected_version: int,
    ) -> Team | None: ...

    @abstractmethod
    async def is_member(self, team_id: UUID, user_id: UUID) -> bool: ...

    @abstractmethod
    async def is_owner(self, team_id: UUID, user_id: UUID) -> bool: ...

    @abstractmethod
    async def get_membership(self, team_id: UUID, user_id: UUID) -> TeamMember | None: ...

    @abstractmethod
    async def list_members(
        self,
        team_id: UUID,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[TeamMember], UUID | None]: ...

    @abstractmethod
    async def delete_member(self, team_id: UUID, user_id: UUID) -> bool: ...

    @abstractmethod
    async def transfer_ownership(
        self,
        team_id: UUID,
        current_owner_id: UUID,
        new_owner_id: UUID,
    ) -> bool: ...
