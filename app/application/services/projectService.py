from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.teamRepository import TeamRepository
from app.domain.erasure_request import ErasureRequest
from app.domain.project import Project


class ProjectNotFound(Exception):
    pass


class ProjectForbidden(Exception):
    pass


class ProjectPreconditionFailed(Exception):
    pass


class ProjectConfirmationRequired(Exception):
    pass


def project_etag(project: Project) -> str:
    return sha256(f"{project.id}:{project.version}".encode()).hexdigest()


class ProjectService:
    def __init__(self, repository: ProjectRepository, teams: TeamRepository):
        self.repository = repository
        self.teams = teams

    async def _authorized(self, project_id: UUID, user_id: UUID) -> Project:
        project = await self.repository.get_by_id(project_id)
        if project is None:
            raise ProjectNotFound
        if not await self.teams.is_member(project.team_id, user_id):
            raise ProjectNotFound
        return project

    async def list(
        self,
        team_id: UUID,
        user_id: UUID,
        cursor: UUID | None,
        search: str | None,
        limit: int,
    ) -> tuple[list[Project], UUID | None]:
        if not await self.teams.is_member(team_id, user_id):
            raise ProjectForbidden
        return await self.repository.list_for_team(team_id, cursor, search, limit)

    async def create(
        self, team_id: UUID, user_id: UUID, name: str, description: str | None
    ) -> Project:
        if not await self.teams.is_member(team_id, user_id):
            raise ProjectForbidden
        return await self.repository.create(
            Project(
                id=uuid4(),
                team_id=team_id,
                name=name,
                description=description,
                created_at=datetime.now(UTC),
            )
        )

    async def get(self, project_id: UUID, user_id: UUID) -> Project:
        return await self._authorized(project_id, user_id)

    async def update(
        self,
        project_id: UUID,
        user_id: UUID,
        name: str | None,
        description: str | None,
        description_provided: bool,
        if_match: str | None,
    ) -> Project:
        project = await self._authorized(project_id, user_id)
        if if_match is None or if_match.strip('"') != project_etag(project):
            raise ProjectPreconditionFailed
        updated = await self.repository.update(
            project_id,
            name,
            description,
            description_provided,
            project.version,
        )
        if updated is None:
            raise ProjectPreconditionFailed
        return updated

    async def request_erasure(
        self, project_id: UUID, user_id: UUID, confirmation: str, key: str
    ) -> ErasureRequest:
        project = await self._authorized(project_id, user_id)
        if not await self.teams.is_owner(project.team_id, user_id):
            raise ProjectForbidden
        if confirmation != project.name:
            raise ProjectConfirmationRequired
        previous = await self.repository.get_erasure_request(project_id, user_id, key)
        if previous is not None:
            return previous
        return await self.repository.create_erasure_request(
            ErasureRequest(
                id=uuid4(),
                project_id=project_id,
                requested_by=user_id,
                created_at=datetime.now(UTC),
            ),
            key,
        )
