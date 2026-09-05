from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.teamRepository import TeamRepository
from app.domain.idempotency import TeamCreationIdempotency
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.infrastructure.persistence.configurations.teamConfigration import TeamModel
from app.infrastructure.persistence.configurations.teamCreationIdempotency import (
    TeamCreationIdempotencyModel,
)
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel


class SqlAlchemyTeamRepository(TeamRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _team(model: TeamModel) -> Team:
        return Team(
            id=model.id,
            name=model.name,
            created_at=model.created_at,
            version=model.version,
        )

    @staticmethod
    def _member(model: TeamMemberModel) -> TeamMember:
        return TeamMember(
            id=model.id,
            team_id=model.team_id,
            user_id=model.user_id,
            role=model.role,
            joined_at=model.joined_at,
        )

    async def list_for_user(self, user_id: UUID, cursor: UUID | None = None, limit: int = 50):
        stmt = (
            select(TeamModel)
            .join(TeamMemberModel)
            .where(TeamMemberModel.user_id == user_id)
            .order_by(TeamModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(TeamModel.id > cursor)
        rows = list((await self.session.scalars(stmt)).all())
        next_cursor = rows.pop().id if len(rows) > limit else None
        return [self._team(row) for row in rows], next_cursor

    async def get_by_id(self, team_id: UUID) -> Team | None:
        model = await self.session.get(TeamModel, team_id)
        return self._team(model) if model is not None else None

    async def get_by_name(self, name: str) -> Team | None:
        model = await self.session.scalar(select(TeamModel).where(TeamModel.name == name))
        return self._team(model) if model is not None else None

    async def create(self, team: Team, owner: TeamMember) -> Team:
        self.session.add(
            TeamModel(
                id=team.id,
                name=team.name,
                created_at=team.created_at,
                version=team.version,
            )
        )
        self.session.add(
            TeamMemberModel(
                id=owner.id,
                team_id=owner.team_id,
                user_id=owner.user_id,
                role=owner.role,
                joined_at=owner.joined_at,
            )
        )
        await self.session.flush()
        return team

    async def get_creation_idempotency(self, user_id: UUID, key: str):
        model = await self.session.get(TeamCreationIdempotencyModel, (user_id, key))
        if model is None:
            return None
        return TeamCreationIdempotency(
            user_id=model.user_id,
            key=model.key,
            request_hash=model.request_hash,
            team_id=model.team_id,
        )

    async def save_creation_idempotency(self, record: TeamCreationIdempotency) -> None:
        self.session.add(
            TeamCreationIdempotencyModel(
                user_id=record.user_id,
                key=record.key,
                request_hash=record.request_hash,
                team_id=record.team_id,
            )
        )
        await self.session.flush()

    async def update_name(
        self,
        team_id: UUID,
        name: str,
        expected_version: int,
    ) -> Team | None:
        result = await self.session.execute(
            update(TeamModel)
            .where(
                TeamModel.id == team_id,
                TeamModel.version == expected_version,
            )
            .values(name=name, version=expected_version + 1)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        model = await self.session.get(TeamModel, team_id)
        return self._team(model) if model is not None else None

    async def is_member(self, team_id: UUID, user_id: UUID) -> bool:
        stmt = select(TeamMemberModel.id).where(
            TeamMemberModel.team_id == team_id,
            TeamMemberModel.user_id == user_id,
        )
        return await self.session.scalar(stmt) is not None

    async def list_members(self, team_id: UUID, cursor: UUID | None = None, limit: int = 50):
        stmt = (
            select(TeamMemberModel)
            .where(TeamMemberModel.team_id == team_id)
            .order_by(TeamMemberModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(TeamMemberModel.id > cursor)
        rows = list((await self.session.scalars(stmt)).all())
        next_cursor = rows.pop().id if len(rows) > limit else None
        return [self._member(row) for row in rows], next_cursor

    async def delete_member(self, team_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(TeamMemberModel).where(
                TeamMemberModel.team_id == team_id,
                TeamMemberModel.user_id == user_id,
            )
        )
        return result.rowcount > 0
