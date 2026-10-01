from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_session
from app.application.erasure_workflow import ErasureWorkflow
from app.domain.user import User
from app.infrastructure.persistence.configurations import (
    AIJobModel,
    ErasureCommandIdempotencyModel,
    ErasureRequestModel,
    ProjectModel,
    TeamMemberModel,
    UserModel,
)
from app.main import create_app
from app.settings import Settings
from tests.acceptance.test_erasure_workflow import Queue, complete, due_again, seed, workflow
from tests.support.fakes import FakeObjectStorage


@pytest.mark.anyio
@pytest.mark.parametrize("team_scoped", [False, True])
async def test_project_delete_replays_durably_and_checks_every_scoped_key(
    db_session_factory: async_sessionmaker[AsyncSession], team_scoped: bool
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        data = await seed(session)
        other = await seed(session)
        second_owner = uuid4()
        session.add(
            UserModel(
                id=second_owner,
                issuer="test",
                subject=str(second_owner),
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(
            TeamMemberModel(
                id=uuid4(),
                team_id=data["team"],
                user_id=second_owner,
                role="owner",
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()

    actor = data["owner"]
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def current_user() -> User:
        return User(
            id=actor,
            issuer="test",
            subject=str(actor),
            display_name="Owner",
            email="owner@example.com",
            created_at=datetime.now(UTC),
        )

    def workflow_factory(session: AsyncSession) -> ErasureWorkflow:
        return workflow(session, storage, queue, redis)

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_current_user] = current_user
    app.state.erasure_workflow_factory = workflow_factory
    canonical = f"/api/v1/projects/{data['project']}"
    alias = f"/api/v1/teams/{data['team']}/projects/{data['project']}"
    path = alias if team_scoped else canonical

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        async def delete(
            key: str, confirmation: str = "Synthetic project", target: str = path
        ) -> Response:
            return await client.request(
                "DELETE",
                target,
                headers={"Idempotency-Key": key},
                json={"confirmation": confirmation},
            )

        first = await delete("original")
        assert first.status_code == 202
        request_id = UUID(first.json()["id"])
        location = first.headers["Location"]
        replay = await delete("original", target=canonical if team_scoped else alias)
        assert replay.status_code == 202
        assert replay.json() == first.json()
        assert replay.headers["Location"] == location

        # Later keys must also be remembered across independent HTTP requests.
        later = await delete("later")
        assert later.status_code == 202 and later.json() == first.json()
        for key in ("original", "later"):
            conflict = await delete(key, "Changed confirmation")
            assert conflict.status_code == 409
            assert conflict.headers["content-type"] == "application/problem+json"
            assert conflict.json()["detail"] == "idempotency_conflict"

        # A different actor has its own key scope even on the same target.
        actor = second_owner
        independent = await delete("original", "Independent payload")
        assert independent.status_code == 202
        assert independent.json()["id"] == str(request_id)
        conflict = await delete("original", "Changed independent payload")
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "idempotency_conflict"

        actor = other["owner"]
        concealed = await delete("original")
        assert concealed.status_code == 404
        other_target = await delete("original", target=f"/api/v1/projects/{other['project']}")
        assert other_target.status_code == 202
        assert other_target.json()["id"] != str(request_id)
        actor = data["owner"]
        wrong_parent = await delete(
            "original", target=f"/api/v1/teams/{other['team']}/projects/{data['project']}"
        )
        assert wrong_parent.status_code == 404

        async with db_session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(ErasureRequestModel)) == 2
            assert await session.scalar(select(func.count()).select_from(AIJobModel)) == 2
            assert (
                await session.scalar(
                    select(func.count()).select_from(ErasureCommandIdempotencyModel)
                )
                == 4
            )
            work = workflow_factory(session)
            await work.run_due()
            for message in queue.messages:
                await complete(work, message)
                await due_again(session, UUID(message.payload.erasure_request_id))
            await work.run_due()
            assert await session.get(ProjectModel, data["project"]) is None
            assert (await work.get(request_id, actor)).status == "completed"
            dispatched = len(queue.messages)

        after_purge = await delete("later")
        assert after_purge.status_code == 202
        assert after_purge.json()["id"] == str(request_id)
        assert after_purge.json()["status"] == "completed"
        assert after_purge.headers["Location"] == location
        assert (await delete("later", "Changed after purge")).status_code == 409
        assert len(queue.messages) == dispatched

        # Replay must reauthorize against current membership, even after purge.
        async with db_session_factory() as session:
            await session.execute(
                update(TeamMemberModel)
                .where(TeamMemberModel.user_id == actor, TeamMemberModel.team_id == data["team"])
                .values(role="member")
            )
            await session.commit()
        assert (await delete("later")).status_code == 403
    await redis.aclose()
