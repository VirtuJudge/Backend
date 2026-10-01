from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_project_service
from app.application.erasure_workflow import ErasureWorkflow
from app.application.services.project_service import ProjectService
from app.domain.user import User
from app.infrastructure.persistence.configurations import (
    ProjectCreationIdempotencyModel,
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemy_project_repository import (
    SqlAlchemyProjectRepository,
)
from app.infrastructure.repositories.sqlalchemy_team_repository import SqlAlchemyTeamRepository
from app.main import create_app
from app.settings import Settings
from tests.support.constants import NOW

pytestmark = pytest.mark.anyio


@pytest.fixture
async def project_client(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> AsyncIterator[AsyncClient]:
    user_id, _, _ = seed_db
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    user = User(user_id, "Member", "test", "member", "member@example.com", NOW)
    app.dependency_overrides[get_current_user] = lambda: user

    async def service() -> AsyncIterator[ProjectService]:
        async with db_session_factory() as session:
            try:
                yield ProjectService(
                    SqlAlchemyProjectRepository(session),
                    SqlAlchemyTeamRepository(session),
                    AsyncMock(spec=ErasureWorkflow),
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_project_service] = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def counts(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with factory() as session:
        projects = await session.scalar(select(func.count()).select_from(ProjectModel))
        records = await session.scalar(
            select(func.count()).select_from(ProjectCreationIdempotencyModel)
        )
        assert projects is not None and records is not None
        return projects, records


async def test_matching_retry_normalizes_missing_description_and_json_order(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    _, team_id, _ = seed_db
    path = f"/api/v1/teams/{team_id}/projects"
    headers = {"Idempotency-Key": "create-1"}
    first = await project_client.post(path, json={"name": "Café"}, headers=headers)
    replay = await project_client.post(
        path, json={"description": None, "name": "Café"}, headers=headers
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert await counts(db_session_factory) == (2, 1)


@pytest.mark.parametrize("payload", [{"name": "Other"}, {"name": "Pitch", "description": ""}])
async def test_conflicting_retry_creates_nothing(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    payload: dict[str, str],
) -> None:
    _, team_id, _ = seed_db
    path = f"/api/v1/teams/{team_id}/projects"
    headers = {"Idempotency-Key": "same"}
    first = await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    conflict = await project_client.post(path, json=payload, headers=headers)
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.headers["content-type"] == "application/problem+json"
    assert conflict.json()["detail"] == "idempotency_key_reused"
    assert await counts(db_session_factory) == (2, 1)


@pytest.mark.parametrize("key", [None, "", "x" * 256])
async def test_creation_rejects_invalid_keys_before_writes(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    key: str | None,
) -> None:
    _, team_id, _ = seed_db
    response = await project_client.post(
        f"/api/v1/teams/{team_id}/projects",
        json={"name": "Pitch"},
        headers={"Idempotency-Key": key} if key is not None else {},
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "validation_failed"
    assert await counts(db_session_factory) == (1, 0)


async def test_distinct_keys_and_teams_create_distinct_projects(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, _ = seed_db
    other_team = uuid4()
    async with db_session_factory() as session:
        session.add(TeamModel(id=other_team, name="Other Team", created_at=NOW))
        await session.flush()
        session.add(TeamMemberModel(team_id=other_team, user_id=user_id, joined_at=NOW))
        await session.commit()
    responses = [
        await project_client.post(
            f"/api/v1/teams/{team}/projects",
            json={"name": "Pitch"},
            headers={"Idempotency-Key": key},
        )
        for team, key in [(team_id, "x"), (team_id, "x" * 255), (other_team, "x")]
    ]
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 3
    assert await counts(db_session_factory) == (4, 3)


async def test_replay_checks_current_membership(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, _ = seed_db
    path = f"/api/v1/teams/{team_id}/projects"
    headers = {"Idempotency-Key": "same"}
    assert (
        await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    ).status_code == 201
    async with db_session_factory() as session:
        await session.execute(delete(TeamMemberModel).where(TeamMemberModel.user_id == user_id))
        await session.commit()
    for key in ["same", "new"]:
        response = await project_client.post(
            path, json={"name": "Pitch"}, headers={"Idempotency-Key": key}
        )
        assert response.status_code == 403
    assert await counts(db_session_factory) == (2, 1)


@pytest.mark.parametrize("purge", [False, True])
async def test_retry_never_recreates_revoked_or_purged_project(
    project_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    purge: bool,
) -> None:
    _, team_id, _ = seed_db
    path = f"/api/v1/teams/{team_id}/projects"
    headers = {"Idempotency-Key": "same"}
    first = await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    assert first.status_code == 201
    project_id = UUID(first.json()["id"])
    async with db_session_factory() as session:
        if purge:
            await SqlAlchemyProjectRepository(session).delete(project_id)
        else:
            await session.execute(
                update(ProjectModel)
                .where(ProjectModel.id == project_id)
                .values(access_revoked_at=NOW)
            )
        await session.commit()
    replay = await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    assert replay.status_code == 409
    assert replay.json()["detail"] == "project_creation_unavailable"
    assert await counts(db_session_factory) == (1 if purge else 2, 1)


async def test_same_key_is_scoped_to_actor(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    other_user = uuid4()
    async with db_session_factory() as session:
        session.add(UserModel(id=other_user, issuer="test", subject="other", created_at=NOW))
        await session.flush()
        session.add(TeamMemberModel(team_id=team_id, user_id=other_user, joined_at=NOW))
        await session.commit()
    ids = []
    for actor in [user_id, other_user]:
        async with db_session_factory() as session:
            service = ProjectService(
                SqlAlchemyProjectRepository(session),
                SqlAlchemyTeamRepository(session),
                AsyncMock(spec=ErasureWorkflow),
            )
            project = await service.create(team_id, actor, "Pitch", None, "same")
            ids.append(project.id)
            await session.commit()
    assert len(set(ids)) == 2
    assert await counts(db_session_factory) == (3, 2)


async def test_replay_returns_current_project_after_edit(
    project_client: AsyncClient,
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    _, team_id, _ = seed_db
    path = f"/api/v1/teams/{team_id}/projects"
    headers = {"Idempotency-Key": "same"}
    created = await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    project_path = f"/api/v1/projects/{created.json()['id']}"
    current = await project_client.get(project_path)
    updated = await project_client.patch(
        project_path, json={"name": "Renamed"}, headers={"If-Match": current.headers["ETag"]}
    )
    assert updated.status_code == 200
    replay = await project_client.post(path, json={"name": "Pitch"}, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == updated.json()
