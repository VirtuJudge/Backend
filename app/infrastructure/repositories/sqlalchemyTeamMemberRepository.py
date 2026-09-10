from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.domain.team_member import TeamMember
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel
from app.infrastructure.persistence.configurations.userConfigration import UserModel


class SqlAlchemyTeamMemberRepository(TeamMemberRepository):
    """Compatibility entry point for membership-specific repository wiring."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_team_and_email(self, team_id: UUID, email: str) -> TeamMember | None:
        """Retrieve a team member by team ID and email."""
        stmt = select(TeamMemberModel).where(
            TeamMemberModel.team_id == team_id,
            TeamMemberModel.user.has(UserModel.email == email),
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        if model is None:
            return None
        return TeamMember(
            id=model.id,
            team_id=model.team_id,
            user_id=model.user_id,
            role=model.role,
            joined_at=model.joined_at,
        )

    async def create(self, team_member: TeamMember) -> TeamMember:
        stmt = insert(TeamMemberModel).values(
            id=team_member.id,
            team_id=team_member.team_id,
            user_id=team_member.user_id,
            role=team_member.role,
            joined_at=team_member.joined_at,
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return team_member
