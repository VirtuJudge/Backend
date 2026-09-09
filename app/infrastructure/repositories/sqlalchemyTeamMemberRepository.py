from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.domain.team_member import TeamMember
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel


class SqlAlchemyTeamMemberRepository(TeamMemberRepository):
    """Compatibility entry point for membership-specific repository wiring."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_team_and_email(self, team_id: str, email: str):
        """Retrieve a team member by team ID and email."""
        stmt = select(TeamMemberModel).where(
            TeamMemberModel.team_id == team_id,
            TeamMemberModel.email == email,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(self, team_member: TeamMember) -> TeamMember:
        stmt = insert(TeamMemberModel).values(
            id=team_member.id,
            team_id=team_member.team_id,
            user_id=team_member.user_id,
            email=team_member.email,
            role=team_member.role,
            joined_at=team_member.joined_at,
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return team_member
