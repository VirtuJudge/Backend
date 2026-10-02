from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.idempotency import InvitationCreationIdempotency
from app.domain.team_invitation import TeamInvitation
from app.domain.team_member import TeamMember


class TeamInvitationRepository(ABC):
    @abstractmethod
    async def create(self, invitation: TeamInvitation, idempotency_key: str) -> TeamInvitation:
        pass

    @abstractmethod
    async def exists_pending_invitation(self, team_id: UUID, email: str) -> bool:
        pass

    @abstractmethod
    async def list_by_team(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 20
    ) -> tuple[list[TeamInvitation], UUID | None]:
        pass

    @abstractmethod
    async def get_creation_idempotency(
        self, actor_id: UUID, team_id: UUID, operation: str, key: str
    ) -> InvitationCreationIdempotency | None:
        pass

    @abstractmethod
    async def create_with_idempotency(
        self, invitation: TeamInvitation, record: InvitationCreationIdempotency
    ) -> InvitationCreationIdempotency:
        """Atomically commit the invitation and key, or return the winning record."""
        pass

    @abstractmethod
    async def get_by_id(self, invitation_id: UUID) -> TeamInvitation | None:
        pass

    @abstractmethod
    async def update(self, invitation: TeamInvitation) -> TeamInvitation:
        pass

    @abstractmethod
    async def get_invitation_by_token(self, token: str) -> tuple[str, str, TeamInvitation] | None:
        pass

    @abstractmethod
    async def accept(self, invitation: TeamInvitation, membership: TeamMember) -> bool:
        """Atomically consume a pending invitation and create its membership."""
        pass
