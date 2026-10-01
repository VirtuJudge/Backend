import asyncio
import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.erasure_workflow import ErasureWorkflow
from app.application.services.project_service import ProjectIdempotencyConflict, ProjectService
from app.domain.idempotency import ProjectCreationIdempotency
from app.domain.project import Project
from app.infrastructure.database import Base
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
from tests.support.constants import NOW

pytestmark = pytest.mark.anyio


async def test_outer_rollback_removes_project_and_key(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        service = ProjectService(
            SqlAlchemyProjectRepository(session),
            SqlAlchemyTeamRepository(session),
            AsyncMock(spec=ErasureWorkflow),
        )
        project = await service.create(team_id, user_id, "Rolled back", None, "retry")
        await session.rollback()
    async with db_session_factory() as session:
        assert await session.get(ProjectModel, project.id) is None
        assert (
            await session.scalar(select(func.count()).select_from(ProjectCreationIdempotencyModel))
            == 0
        )
        service = ProjectService(
            SqlAlchemyProjectRepository(session),
            SqlAlchemyTeamRepository(session),
            AsyncMock(spec=ErasureWorkflow),
        )
        retry = await service.create(team_id, user_id, "Rolled back", None, "retry")
        await session.commit()
        assert retry.id != project.id


async def test_repository_losing_race_leaves_no_extra_project_and_operation_is_scoped(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    records = []
    for operation in ["create_project", "create_project", "other_operation"]:
        project = Project(uuid4(), team_id, "Pitch", None, NOW)
        record = ProjectCreationIdempotency(
            user_id, team_id, operation, "same", "a" * 64, project.id
        )
        async with db_session_factory() as session:
            records.append(
                await SqlAlchemyProjectRepository(session).create_with_idempotency(project, record)
            )
            await session.commit()
    assert records[0] == records[1]
    assert records[2].project_id != records[0].project_id
    async with db_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProjectModel)) == 3
        assert (
            await session.scalar(select(func.count()).select_from(ProjectCreationIdempotencyModel))
            == 2
        )


@pytest.fixture
async def postgres_creation_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("PROJECT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PROJECT_TEST_DATABASE_URL not configured")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.parametrize("conflicting", [False, True])
async def test_postgres_concurrent_creation_is_atomic(
    postgres_creation_factory: async_sessionmaker[AsyncSession], conflicting: bool
) -> None:
    factory = postgres_creation_factory
    user_id, team_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(UserModel(id=user_id, issuer="test", subject=str(user_id), created_at=NOW))
        session.add(TeamModel(id=team_id, name=f"Team {team_id}", created_at=NOW))
        await session.flush()
        session.add(TeamMemberModel(team_id=team_id, user_id=user_id, joined_at=NOW))
        await session.commit()
    ready = asyncio.Event()
    arrivals = 0

    class RacingRepository(SqlAlchemyProjectRepository):
        async def get_creation_idempotency(
            self, actor: UUID, team: UUID, operation: str, key: str
        ) -> ProjectCreationIdempotency | None:
            nonlocal arrivals
            previous = await super().get_creation_idempotency(actor, team, operation, key)
            if previous is None:
                arrivals += 1
                if arrivals == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=10)
            return previous

    async def create(name: str) -> Project | ProjectIdempotencyConflict:
        async with factory() as session:
            service = ProjectService(
                RacingRepository(session),
                SqlAlchemyTeamRepository(session),
                AsyncMock(spec=ErasureWorkflow),
            )
            try:
                project = await service.create(team_id, user_id, name, None, "same")
                await session.commit()
                return project
            except ProjectIdempotencyConflict as error:
                await session.rollback()
                return error

    try:
        results = await asyncio.wait_for(
            asyncio.gather(create("Pitch"), create("Other" if conflicting else "Pitch")), timeout=20
        )
        projects = [result for result in results if isinstance(result, Project)]
        if conflicting:
            assert len(projects) == 1
            assert sum(isinstance(result, ProjectIdempotencyConflict) for result in results) == 1
        else:
            assert len(projects) == 2
            assert projects[0].id == projects[1].id
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ProjectModel)
                    .where(ProjectModel.team_id == team_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ProjectCreationIdempotencyModel)
                    .where(ProjectCreationIdempotencyModel.team_id == team_id)
                )
                == 1
            )
    finally:
        async with factory() as session:
            await session.execute(delete(TeamModel).where(TeamModel.id == team_id))
            await session.execute(delete(UserModel).where(UserModel.id == user_id))
            await session.commit()
