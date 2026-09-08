from abc import ABC, abstractmethod

from app.application.interfaces.teamRepository import TeamRepository
from app.domain.team_member import TeamMember


class TeamMemberRepository(TeamRepository, ABC):
    """Repository contract for team membership operations."""

    @abstractmethod
    async def get_by_team_and_email(self, team_id: str, email: str):
        """Retrieve a team member by team ID and email."""
        pass

    @abstractmethod
    async def create(self, team_member: TeamMember) -> TeamMember:
        """Create a new team member."""
        pass