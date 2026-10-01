from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.erasure import ErasureConflict, ErasureForbidden, ErasureNotFound
from app.infrastructure.persistence.configurations import (
    AIJobModel,
    ErasureRequestModel,
    ProjectModel,
    TeamMemberModel,
)
from app.main import project_service_factory
from tests.acceptance.test_erasure_workflow import Queue, seed, workflow
from tests.support.fakes import FakeObjectStorage


@pytest.mark.anyio
@pytest.mark.parametrize("entry_point", ["delete", "request_erasure"])
@pytest.mark.parametrize(
    "confirmation",
    [None, "", "Wrong Name", "synthetic project", " Synthetic project", "Synthetic project "],
)
async def test_project_service_rejects_missing_or_mismatched_confirmation(
    db_session_factory: async_sessionmaker[AsyncSession],
    entry_point: str,
    confirmation: str | None,
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with db_session_factory() as session:
        target = await seed(session)
        service = project_service_factory(
            session, workflow(session, FakeObjectStorage(), Queue(), redis)
        )
        with pytest.raises(ErasureConflict, match="confirmation_required"):
            await getattr(service, entry_point)(
                target["project"], target["owner"], confirmation, "delete"
            )
        assert await service.repository.get_by_id(target["project"]) is not None
        assert await session.scalar(select(func.count()).select_from(ErasureRequestModel)) == 0
        assert await session.scalar(select(func.count()).select_from(AIJobModel)) == 0
    await redis.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("entry_point", ["delete", "request_erasure"])
@pytest.mark.parametrize("actor", ["member", "outsider"])
async def test_project_service_authorizes_erasure_before_revocation(
    db_session_factory: async_sessionmaker[AsyncSession], entry_point: str, actor: str
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with db_session_factory() as session:
        target, unrelated = await seed(session), await seed(session)
        if actor == "member":
            await session.execute(
                update(TeamMemberModel)
                .where(TeamMemberModel.user_id == target["owner"])
                .values(role="member")
            )
            await session.commit()
        service = project_service_factory(
            session, workflow(session, FakeObjectStorage(), Queue(), redis)
        )
        actor_id = target["owner"] if actor == "member" else unrelated["owner"]
        error = ErasureForbidden if actor == "member" else ErasureNotFound
        with pytest.raises(error):
            await getattr(service, entry_point)(
                target["project"], actor_id, "Synthetic project", "delete"
            )
        assert await service.repository.get_by_id(target["project"]) is not None
        assert await session.scalar(select(func.count()).select_from(ErasureRequestModel)) == 0
        assert await session.scalar(select(func.count()).select_from(AIJobModel)) == 0
    await redis.aclose()


@pytest.mark.anyio
async def test_project_service_conceals_wrong_parent_and_unknown_project(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with db_session_factory() as session:
        target = await seed(session)
        service = project_service_factory(
            session, workflow(session, FakeObjectStorage(), Queue(), redis)
        )
        with pytest.raises(ErasureNotFound):
            await service.delete(
                target["project"],
                target["owner"],
                "Synthetic project",
                "delete",
                expected_team_id=uuid4(),
            )
        with pytest.raises(ErasureNotFound):
            await service.delete(uuid4(), target["owner"], "Synthetic project", "delete")
        assert await session.scalar(select(func.count()).select_from(ErasureRequestModel)) == 0
        assert await session.get(ProjectModel, target["project"]) is not None
    await redis.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("entry_point", ["delete", "request_erasure"])
async def test_project_service_propagates_coordinator_failure(
    db_session_factory: async_sessionmaker[AsyncSession], entry_point: str
) -> None:
    async with db_session_factory() as session:
        target = await seed(session)
        coordinator = AsyncMock()
        coordinator.request.side_effect = RuntimeError("coordinator unavailable")
        service = project_service_factory(session, coordinator)
        with pytest.raises(RuntimeError, match="coordinator unavailable"):
            await getattr(service, entry_point)(
                target["project"], target["owner"], "Synthetic project", "delete"
            )
        assert await service.repository.get_by_id(target["project"]) is not None
        assert await session.scalar(select(func.count()).select_from(ErasureRequestModel)) == 0
