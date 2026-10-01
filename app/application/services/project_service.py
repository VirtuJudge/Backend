import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from app.application.erasure_workflow import ErasureWorkflow
from app.application.ports.project_repository import ProjectRepository
from app.application.ports.team_repository import TeamRepository
from app.domain.erasure import Erasure
from app.domain.idempotency import ProjectCreationIdempotency
from app.domain.project import Project


class ProjectNotFound(Exception):
    pass


class ProjectForbidden(Exception):
    pass


class ProjectPreconditionFailed(Exception):
    pass


class ProjectIdempotencyConflict(Exception):
    pass


class ProjectCreationUnavailable(Exception):
    pass


def project_etag(project: Project) -> str:
    return sha256(f"{project.id}:{project.version}".encode()).hexdigest()


class ProjectService:
    def __init__(
        self, repository: ProjectRepository, teams: TeamRepository, erasure: ErasureWorkflow
    ):
        self.repository = repository
        self.teams = teams
        self.erasure = erasure

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
        self, team_id: UUID, user_id: UUID, name: str, description: str | None, key: str
    ) -> Project:
        if not await self.teams.is_member(team_id, user_id):
            raise ProjectForbidden
        request_hash = sha256(
            json.dumps(
                {"name": name, "description": description},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        operation = "create_project"
        previous = await self.repository.get_creation_idempotency(user_id, team_id, operation, key)
        if previous is None:
            project = Project(
                id=uuid4(),
                team_id=team_id,
                name=name,
                description=description,
                created_at=datetime.now(UTC),
            )
            previous = await self.repository.create_with_idempotency(
                project,
                ProjectCreationIdempotency(
                    user_id, team_id, operation, key, request_hash, project.id
                ),
            )
        if previous.request_hash != request_hash:
            raise ProjectIdempotencyConflict
        created = await self.repository.get_by_id(previous.project_id)
        if created is None:
            raise ProjectCreationUnavailable
        return created

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

    async def delete(
        self,
        project_id: UUID,
        user_id: UUID,
        confirmation: str | None,
        key: str,
        *,
        expected_team_id: UUID | None = None,
    ) -> Erasure:
        return await self.erasure.request(
            "project",
            project_id,
            user_id,
            key,
            confirmation,
            expected_team_id=expected_team_id,
        )

    async def request_erasure(
        self, project_id: UUID, user_id: UUID, confirmation: str, key: str
    ) -> Erasure:
        return await self.delete(project_id, user_id, confirmation, key)
