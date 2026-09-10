from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.team_member import TeamMember


class TeamMemberRepository(ABC):
    """Repository contract for team membership operations."""

    @abstractmethod
    async def get_by_team_and_email(self, team_id: UUID, email: str) -> TeamMember | None:
        """Retrieve a team member by team ID and email."""
        pass

    @abstractmethod
    async def create_with_same_transaction(self, team_member: TeamMember) -> TeamMember:
        """Create a new team member."""
        pass
