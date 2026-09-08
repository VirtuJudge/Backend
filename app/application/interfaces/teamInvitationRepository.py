from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from app.domain.team_invitation import TeamInvitation


class TeamInvitationRepository(ABC):
    @abstractmethod
    async def create(self, invitation: TeamInvitation, idempotency_key: str) -> TeamInvitation:
        pass

    @abstractmethod
    async def exists_pending_invitation(self, team_id: UUID, email: str) -> bool:
        pass
    
    @abstractmethod
    async def list_by_team(self, team_id: UUID, cursor: UUID | None = None, limit: int = 20) -> list[TeamInvitation]:
        pass

    @abstractmethod
    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> TeamInvitation | None:
        pass

    @abstractmethod
    async def get_by_id(self, invitation_id: UUID) -> Optional[TeamInvitation]:
        pass

    @abstractmethod
    async def update(self, invitation: TeamInvitation) -> TeamInvitation:
        pass

    @abstractmethod
    async def get_invitation_by_token(self, token: str) -> tuple[str, str, TeamInvitation] | None:
        pass