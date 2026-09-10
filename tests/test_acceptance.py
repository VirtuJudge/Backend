import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException

from app.api.dependencies.teamAuthorization import get_team_owner
from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.teamRepository import TeamRepository
from app.application.interfaces.userRepository import UserRepository
from app.application.services.projectService import (
    ProjectForbidden,
    ProjectNotFound,
    ProjectPreconditionFailed,
    ProjectService,
    project_etag,
)
from app.application.services.teamService import (
    TeamOwnerCannotBeRemoved,
    TeamPreconditionFailed,
    TeamService,
    team_etag,
)
from app.application.services.userService import UserService
from app.domain.erasure_request import ErasureRequest
from app.domain.idempotency import TeamCreationIdempotency
from app.domain.project import Project
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.domain.user import User
from app.infrastructure.auth.oidc import OIDCTokenVerifier

NOW = datetime.now(UTC)
Result = TypeVar("Result")
OWNER_ID = uuid4()
MEMBER_ID = uuid4()
OUTSIDER_ID = uuid4()
TEAM_ID = uuid4()


class FakeTeamRepository(TeamRepository):
    def __init__(self) -> None:
        self.teams: dict[UUID, Team] = {TEAM_ID: Team(id=TEAM_ID, name="Team", created_at=NOW)}
        self.members: dict[tuple[UUID, UUID], TeamMember] = {
            (TEAM_ID, OWNER_ID): TeamMember(
                id=uuid4(),
                team_id=TEAM_ID,
                user_id=OWNER_ID,
                role="owner",
                joined_at=NOW,
            ),
            (TEAM_ID, MEMBER_ID): TeamMember(
                id=uuid4(),
                team_id=TEAM_ID,
                user_id=MEMBER_ID,
                role="member",
                joined_at=NOW,
            ),
        }
        self.idempotency: dict[tuple[UUID, str], TeamCreationIdempotency] = {}
        self.created_memberships: list[TeamMember] = []
        self.last_expected_version: int | None = None

    async def list_for_user(
        self, user_id: UUID, cursor: UUID | None = None, limit: int = 50
    ) -> tuple[list[Team], UUID | None]:
        teams = [team for team in self.teams.values() if await self.is_member(team.id, user_id)]
        return teams[:limit], None

    async def get_by_id(self, team_id: UUID) -> Team | None:
        return self.teams.get(team_id)

    async def get_by_name(self, name: str) -> Team | None:
        return next((team for team in self.teams.values() if team.name == name), None)

    async def create(self, team: Team, owner: TeamMember) -> Team:
        self.teams[team.id] = team
        self.members[(owner.team_id, owner.user_id)] = owner
        self.created_memberships.append(owner)
        return team

    async def is_owner(self, team_id: UUID, user_id: UUID) -> bool:
        member = self.members.get((team_id, user_id))
        return member is not None and member.role == "owner"

    async def get_creation_idempotency(
        self, user_id: UUID, key: str
    ) -> TeamCreationIdempotency | None:
        return self.idempotency.get((user_id, key))

    async def save_creation_idempotency(self, record: TeamCreationIdempotency) -> None:
        self.idempotency[(record.user_id, record.key)] = record

    async def update_name(self, team_id: UUID, name: str, expected_version: int) -> Team | None:
        self.last_expected_version = expected_version
        team = self.teams.get(team_id)
        if team is None or team.version != expected_version:
            return None
        updated = Team(
            id=team.id,
            name=name,
            created_at=team.created_at,
            version=team.version + 1,
        )
        self.teams[team_id] = updated
        return updated

    async def is_member(self, team_id: UUID, user_id: UUID) -> bool:
        return (team_id, user_id) in self.members

    async def get_membership(self, team_id: UUID, user_id: UUID) -> TeamMember | None:
        return self.members.get((team_id, user_id))

    async def list_members(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 50
    ) -> tuple[list[TeamMember], UUID | None]:
        members = [
            member for (member_team, _), member in self.members.items() if member_team == team_id
        ]
        return members, None

    async def delete_member(self, team_id: UUID, user_id: UUID) -> bool:
        member = self.members.get((team_id, user_id))
        if member is None or member.role == "owner":
            return False
        del self.members[(team_id, user_id)]
        return True

    async def transfer_ownership(
        self, team_id: UUID, current_owner_id: UUID, new_owner_id: UUID
    ) -> bool:
        current = self.members.get((team_id, current_owner_id))
        new = self.members.get((team_id, new_owner_id))
        if current is None or current.role != "owner" or new is None or new.role != "member":
            return False
        self.members[(team_id, current_owner_id)] = TeamMember(
            id=current.id,
            team_id=team_id,
            user_id=current_owner_id,
            role="member",
            joined_at=current.joined_at,
        )
        self.members[(team_id, new_owner_id)] = TeamMember(
            id=new.id,
            team_id=team_id,
            user_id=new_owner_id,
            role="owner",
            joined_at=new.joined_at,
        )
        return True


class FakeProjectRepository(ProjectRepository):
    def __init__(self, project: Project) -> None:
        self.project = project
        self.erasure_requests: dict[str, ErasureRequest] = {}
        self.last_expected_version: int | None = None

    async def list_for_team(
        self,
        team_id: UUID,
        cursor: UUID | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Project], UUID | None]:
        if self.project.team_id != team_id:
            return [], None
        if search and search.lower() not in self.project.name.lower():
            return [], None
        return [self.project], None

    async def get_by_id(self, project_id: UUID) -> Project | None:
        return self.project if self.project.id == project_id else None

    async def create(self, project: Project) -> Project:
        self.project = project
        return project

    async def update(
        self,
        project_id: UUID,
        name: str | None,
        description: str | None,
        description_provided: bool,
        expected_version: int,
    ) -> Project | None:
        self.last_expected_version = expected_version
        if self.project.version != expected_version:
            return None
        self.project = Project(
            id=self.project.id,
            team_id=self.project.team_id,
            name=name if name is not None else self.project.name,
            description=description if description_provided else self.project.description,
            created_at=self.project.created_at,
            version=expected_version + 1,
        )
        return self.project

    async def get_erasure_request(
        self, project_id: UUID, requested_by: UUID, key: str
    ) -> ErasureRequest | None:
        return self.erasure_requests.get(key)

    async def create_erasure_request(self, request: ErasureRequest, key: str) -> ErasureRequest:
        self.erasure_requests[key] = request
        return request


class FakeUserRepository(UserRepository):
    def __init__(self) -> None:
        self.user: User | None = None

    async def get_by_identity(self, issuer: str, subject: str) -> User | None:
        return self.user

    async def get_by_id(self, user_id: UUID) -> User | None:
        if self.user is not None and self.user.id == user_id:
            return self.user
        return None

    async def create(self, user: User) -> User:
        self.user = user
        return user


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def test_oidc_verifier_delegates_signature_issuer_audience_and_expiry_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeJWKClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> object:
            return type("Key", (), {"key": "public-key"})()

    monkeypatch.setattr("app.infrastructure.auth.oidc.PyJWKClient", FakeJWKClient)
    calls: list[dict[str, object]] = []

    def decode(
        token: str,
        key: object,
        algorithms: list[str],
        issuer: str,
        audience: str,
        options: dict[str, object],
    ) -> dict[str, object]:
        calls.append(
            {
                "token": token,
                "key": key,
                "algorithms": algorithms,
                "issuer": issuer,
                "audience": audience,
                "options": options,
            }
        )
        return {
            "iss": issuer,
            "aud": audience,
            "sub": "subject",
            "exp": 9999999999,
            "display_name": "User Name",
        }

    monkeypatch.setattr("app.infrastructure.auth.oidc.jwt.decode", decode)
    verifier = OIDCTokenVerifier("issuer", "audience", "https://issuer/jwks")

    claims = verifier.verify("signed-token")

    assert claims["iss"] == "issuer"
    assert calls == [
        {
            "token": "signed-token",
            "key": "public-key",
            "algorithms": ["RS256", "ES256"],
            "issuer": "issuer",
            "audience": "audience",
            "options": {"require": ["exp", "sub"]},
        }
    ]


def test_local_user_is_provisioned_from_issuer_and_subject() -> None:
    repository = FakeUserRepository()
    service = UserService(repository)
    user = run(
        service.get_or_create_user(
            "issuer",
            "subject",
            "user@example.com",
            display_name="Test User",
        )
    )

    assert isinstance(user, User)
    assert user.issuer == "issuer"
    assert user.subject == "subject"
    assert user.display_name == "Test User"
    assert repository.user == user


def test_team_creation_is_idempotent_and_creates_owner_membership() -> None:
    repository = FakeTeamRepository()
    service = TeamService(repository)

    first = run(service.create(OWNER_ID, "New Team", "request-1"))
    second = run(service.create(OWNER_ID, "New Team", "request-1"))

    assert first == second
    assert len(repository.created_memberships) == 1
    assert repository.created_memberships[0].role == "owner"
    assert repository.created_memberships[0].user_id == OWNER_ID


def test_outside_user_cannot_list_or_modify_team_projects() -> None:
    team_repository = FakeTeamRepository()
    project = Project(uuid4(), TEAM_ID, "Project", None, NOW)
    project_repository = FakeProjectRepository(project)
    service = ProjectService(project_repository, team_repository)

    with pytest.raises(ProjectForbidden):
        run(service.list(TEAM_ID, OUTSIDER_ID, None, None, 50))

    with pytest.raises(ProjectNotFound):
        run(service.get(project.id, OUTSIDER_ID))

    with pytest.raises(ProjectNotFound):
        run(service.get(project.id, OUTSIDER_ID))

    with pytest.raises(ProjectForbidden):
        run(service.create(TEAM_ID, OUTSIDER_ID, "Other", None))


def test_team_member_can_create_and_list_projects() -> None:
    team_repository = FakeTeamRepository()
    project_repository = FakeProjectRepository(Project(uuid4(), TEAM_ID, "Project", None, NOW))
    service = ProjectService(project_repository, team_repository)

    created = run(service.create(TEAM_ID, MEMBER_ID, "New Project", "description"))
    listed, cursor = run(service.list(TEAM_ID, MEMBER_ID, None, None, 50))

    assert isinstance(created, Project)
    assert cursor is None
    assert listed[0].team_id == TEAM_ID


def test_owner_cannot_be_removed_until_ownership_is_transferred() -> None:
    repository = FakeTeamRepository()
    service = TeamService(repository)

    with pytest.raises(TeamOwnerCannotBeRemoved):
        run(service.remove_member(TEAM_ID, OWNER_ID))

    transferred = run(service.transfer_ownership(TEAM_ID, OWNER_ID, MEMBER_ID))
    assert transferred.role == "owner"
    assert run(service.remove_member(TEAM_ID, OWNER_ID)) is True


def test_member_is_rejected_by_owner_authorization_dependency() -> None:
    member = TeamMember(
        id=uuid4(),
        team_id=TEAM_ID,
        user_id=MEMBER_ID,
        role="member",
        joined_at=NOW,
    )

    with pytest.raises(HTTPException) as error:
        run(get_team_owner(member))

    assert error.value.status_code == 403


def test_team_and_project_updates_require_current_etag() -> None:
    team_repository = FakeTeamRepository()
    team_service = TeamService(team_repository)
    team = run(team_service.get(TEAM_ID))

    updated = run(team_service.update_name(TEAM_ID, "Renamed", team_etag(team)))
    assert updated.version == team.version + 1
    with pytest.raises(TeamPreconditionFailed):
        run(team_service.update_name(TEAM_ID, "Stale", team_etag(team)))

    project = Project(uuid4(), TEAM_ID, "Project", None, NOW)
    project_repository = FakeProjectRepository(project)
    project_service = ProjectService(project_repository, team_repository)
    updated_project = run(
        project_service.update(
            project.id,
            MEMBER_ID,
            "Renamed Project",
            None,
            False,
            project_etag(project),
        )
    )
    assert updated_project.version == project.version + 1
    with pytest.raises(ProjectPreconditionFailed):
        run(
            project_service.update(
                project.id,
                MEMBER_ID,
                "Stale Project",
                None,
                False,
                project_etag(project),
            )
        )


def test_project_erasure_request_is_idempotent() -> None:
    team_repository = FakeTeamRepository()
    project = Project(uuid4(), TEAM_ID, "Project", None, NOW)
    project_repository = FakeProjectRepository(project)
    service = ProjectService(project_repository, team_repository)

    first = run(
        service.request_erasure(
            project.id,
            OWNER_ID,
            "Project",
            "erase-1",
        )
    )

    second = run(
        service.request_erasure(
            project.id,
            OWNER_ID,
            "Project",
            "erase-1",
        )
    )

    assert first == second
    assert len(project_repository.erasure_requests) == 1


@pytest.mark.parametrize(
    "error",
    [
        jwt.InvalidIssuerError,
        jwt.InvalidAudienceError,
        jwt.InvalidSignatureError,
        jwt.ExpiredSignatureError,
    ],
)
def test_oidc_rejects_invalid_token_conditions(
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
) -> None:
    class FakeJWKClient:
        def __init__(self, url: str) -> None:
            pass

        def get_signing_key_from_jwt(self, token: str) -> object:
            return type("Key", (), {"key": "public-key"})()

    monkeypatch.setattr("app.infrastructure.auth.oidc.PyJWKClient", FakeJWKClient)

    def decode(*args: object, **kwargs: object) -> dict[str, object]:
        raise error()

    monkeypatch.setattr("app.infrastructure.auth.oidc.jwt.decode", decode)
    verifier = OIDCTokenVerifier("issuer", "audience", "https://issuer/jwks")

    with pytest.raises(error):
        verifier.verify("invalid-token")
