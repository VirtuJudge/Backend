from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

class SqlAlchemyTeamMemberRepository(TeamMemberRepository):
    """Compatibility entry point for membership-specific repository wiring."""
    def __init__(self, session: AsyncSession):
        self.session = session
        
    async def get_by_team_and_email(self, team_id: str, email: str):
        """Retrieve a team member by team ID and email."""
        stmt = (
            select(TeamMemberModel)
            .where(
                TeamMemberModel.team_id == team_id,
                TeamMemberModel.email == email,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()