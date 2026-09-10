from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import NO_VALUE

from app.application.ports.team_repository import TeamRepository
from app.domain.idempotency import TeamCreationIdempotency
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.infrastructure.persistence.configurations.team_configuration import TeamModel
from app.infrastructure.persistence.configurations.team_creation_idempotency import (
    TeamCreationIdempotencyModel,
)
from app.infrastructure.persistence.configurations.team_member_configuration import TeamMemberModel


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
        user = model.__dict__.get("user")
        if user is NO_VALUE:
            user = None
        display_name = getattr(user, "display_name", None) if user else None
        return TeamMember(
            id=model.id,
            team_id=model.team_id,
            user_id=model.user_id,
            role=model.role,
            joined_at=model.joined_at,
            display_name=display_name,
        )

    async def list_for_user(
        self,
        user_id: UUID,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Team], UUID | None]:
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
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
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

    async def get_creation_idempotency(
        self, user_id: UUID, key: str
    ) -> TeamCreationIdempotency | None:
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

    async def update_name(self, team_id: UUID, name: str, expected_version: int) -> Team | None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(TeamModel)
                .where(TeamModel.id == team_id, TeamModel.version == expected_version)
                .values(name=name, version=expected_version + 1)
            ),
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        model = await self.session.get(TeamModel, team_id)
        return self._team(model) if model is not None else None

    async def is_member(self, team_id: UUID, user_id: UUID) -> bool:
        return await self.get_membership(team_id, user_id) is not None

    async def is_owner(self, team_id: UUID, user_id: UUID) -> bool:
        membership = await self.get_membership(team_id, user_id)
        return membership is not None and membership.role == "owner"

    async def get_membership(self, team_id: UUID, user_id: UUID) -> TeamMember | None:
        stmt = (
            select(TeamMemberModel)
            .options(joinedload(TeamMemberModel.user))
            .where(
                TeamMemberModel.team_id == team_id,
                TeamMemberModel.user_id == user_id,
            )
        )
        model = await self.session.scalar(stmt)
        return self._member(model) if model is not None else None

    async def list_members(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 50
    ) -> tuple[list[TeamMember], UUID | None]:
        stmt = (
            select(TeamMemberModel)
            .options(joinedload(TeamMemberModel.user))
            .where(TeamMemberModel.team_id == team_id)
            .order_by(TeamMemberModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(TeamMemberModel.id > cursor)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
        return [self._member(row) for row in rows], next_cursor

    async def delete_member(self, team_id: UUID, user_id: UUID) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                delete(TeamMemberModel).where(
                    TeamMemberModel.team_id == team_id,
                    TeamMemberModel.user_id == user_id,
                    TeamMemberModel.role != "owner",
                )
            ),
        )
        return bool(result.rowcount and result.rowcount > 0)

    async def transfer_ownership(
        self,
        team_id: UUID,
        current_owner_id: UUID,
        new_owner_id: UUID,
    ) -> bool:
        if current_owner_id == new_owner_id:
            return True
        promoted = cast(
            CursorResult[Any],
            await self.session.execute(
                update(TeamMemberModel)
                .where(
                    TeamMemberModel.team_id == team_id,
                    TeamMemberModel.user_id == new_owner_id,
                    TeamMemberModel.role == "member",
                )
                .values(role="owner")
            ),
        )
        if promoted.rowcount != 1:
            return False
        demoted = cast(
            CursorResult[Any],
            await self.session.execute(
                update(TeamMemberModel)
                .where(
                    TeamMemberModel.team_id == team_id,
                    TeamMemberModel.user_id == current_owner_id,
                    TeamMemberModel.role == "owner",
                )
                .values(role="member")
            ),
        )
        if demoted.rowcount != 1:
            raise RuntimeError("ownership transfer could not demote the current owner")
        await self.session.flush()
        return True
