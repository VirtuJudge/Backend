from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from app.application.interfaces.teamRepository import TeamRepository
from app.domain.team import Team
from app.domain.team_member import TeamMember


class TeamNotFound(Exception):
    pass


class TeamForbidden(Exception):
    pass


class TeamNameConflict(Exception):
    pass


class TeamPreconditionFailed(Exception):
    pass


def team_etag(team: Team) -> str:
    value = f"{team.id}:{team.name}:{team.created_at.isoformat()}"
    return sha256(value.encode()).hexdigest()


class TeamService:

    def __init__(self, repository: TeamRepository):
        self.repository = repository

    async def list(self, user_id: UUID, cursor: UUID | None, limit: int):
        return await self.repository.list_for_user(user_id, cursor, limit)

    async def create(self, user_id: UUID, name: str) -> Team:
        if await self.repository.get_by_name(name) is not None:
            raise TeamNameConflict
        now = datetime.now(UTC)
        team = Team(id=uuid4(), name=name, created_at=now)
        owner = TeamMember(
            id=uuid4(), team_id=team.id, user_id=user_id, role="owner", joined_at=now
        )
        return await self.repository.create(team, owner)

    async def get_authorized(self, team_id: UUID, user_id: UUID) -> Team:
        team = await self.repository.get_by_id(team_id)
        if team is None:
            raise TeamNotFound
        if not await self.repository.is_member(team_id, user_id):
            raise TeamForbidden
        return team

    async def update_name(self, team_id: UUID, user_id: UUID, name: str, if_match: str | None):
        team = await self.get_authorized(team_id, user_id)
        if if_match is None or if_match.strip('"') != team_etag(team):
            raise TeamPreconditionFailed
        conflict = await self.repository.get_by_name(name)
        if conflict is not None and conflict.id != team_id:
            raise TeamNameConflict
        return await self.repository.update_name(team_id, name)

    async def members(self, team_id: UUID, user_id: UUID, cursor: UUID | None, limit: int):
        await self.get_authorized(team_id, user_id)
        return await self.repository.list_members(team_id, cursor, limit)

    async def remove_member(self, team_id: UUID, user_id: UUID, member_id: UUID):
        await self.get_authorized(team_id, user_id)
        return await self.repository.delete_member(team_id, member_id)