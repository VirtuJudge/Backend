import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.asset import StorageUnavailable
from app.domain.erasure import Erasure, ErasureConflict, ErasureNotFound
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    AssetModel,
    ErasureRequestModel,
    PracticeSessionModel,
    ProjectErasureRequestModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.sqlalchemy_erasure_repository import (
    SqlAlchemyErasureRepository,
)
from tests.acceptance.test_erasure_workflow import Queue, complete, due_again, seed, workflow
from tests.support.fakes import FakeObjectStorage


@pytest.mark.anyio
async def test_session_erasure_keeps_shared_media_and_other_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        data = await seed(session)
        sibling = uuid4()
        now = datetime.now(UTC)
        session.add(
            PracticeSessionModel(
                id=sibling,
                project_id=data["project"],
                created_by=data["owner"],
                status=SessionStatus.READY,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            SessionManifestModel(
                id=uuid4(),
                session_id=sibling,
                presentation_version_id=data["version"],
                rubric_id="startup_pitch",
                rubric_version=1,
            )
        )
        await session.commit()
        await storage.put_object(data["key"], b"synthetic", "video/mp4")
        qa_key = f"projects/{data['project']}/sessions/{data['session']}/attempts/1/qa.json"
        await storage.put_object(qa_key, b"synthetic", "application/json")
        work = workflow(session, storage, queue, redis)
        request = await work.request("practice_session", data["session"], data["owner"], "key")
        assert request.inventory["asset_ids"] == []
        await work.run_due()
        await complete(work, queue.messages[0])
        await due_again(session, request.id)
        await work.run_due()
        assert (await work.get(request.id, data["owner"])).status == "completed"
        assert (
            await session.get(PracticeSessionModel, data["session"], populate_existing=True) is None
        )
        assert await session.get(PracticeSessionModel, sibling) is not None
        assert await session.get(AssetModel, data["asset"]) is not None
        assert data["key"] in storage.objects
        assert qa_key not in storage.objects
    await redis.aclose()


@pytest.mark.anyio
async def test_expired_lease_is_reclaimed_and_stale_runner_cannot_write(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        data = await seed(session)
        repository = SqlAlchemyErasureRepository(session)
        now = datetime.now(UTC)
        request = await repository.accept(
            "project", data["project"], data["owner"], "key", "Synthetic project", now
        )
        first = await repository.claim(now, 30)
        assert first is not None
        assert await repository.claim(now, 30) is None
        second = await repository.claim(now + timedelta(seconds=31), 30)
        assert second is not None and second.id == request.id
        assert second.lease_token != first.lease_token
        with pytest.raises(ErasureConflict):
            await repository.finish_step(first, "pending_work", now)
        await session.rollback()
        await repository.finish_step(second, "pending_work", now)


@pytest.mark.anyio
async def test_legacy_requests_keep_actor_and_original_deadline(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        data = await seed(session)
        original = datetime.now(UTC) - timedelta(days=2)
        session.add(
            ProjectErasureRequestModel(
                id=uuid4(),
                project_id=data["project"],
                requested_by=data["owner"],
                idempotency_key="legacy",
                created_at=original,
            )
        )
        await session.commit()
        repository = SqlAlchemyErasureRepository(session)
        assert await repository.adopt_legacy(datetime.now(UTC), 50) == 1
        assert await repository.adopt_legacy(datetime.now(UTC), 50) == 0
        row = await session.scalar(select(ErasureRequestModel))
        assert row is not None
        request = await repository.get(row.id, data["owner"])
        assert request.requested_by == data["owner"]
        assert request.requested_at == original
        assert request.deadline_at == original + timedelta(hours=24)


@pytest.mark.anyio
async def test_simultaneous_acceptance_and_claims_are_scoped(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("Concurrent lease claims require PostgreSQL")
        data = await seed(session)
    now = datetime.now(UTC)

    async def accept() -> Erasure:
        async with db_session_factory() as session:
            return await SqlAlchemyErasureRepository(session).accept(
                "project", data["project"], data["owner"], "key", "Synthetic project", now
            )

    requests = await asyncio.gather(accept(), accept())
    assert requests[0].id == requests[1].id

    async def claim() -> Erasure | None:
        async with db_session_factory() as session:
            return await SqlAlchemyErasureRepository(session).claim(now, 30)

    claims = await asyncio.gather(claim(), claim())
    assert sum(request is not None for request in claims) == 1


@pytest.mark.anyio
async def test_parent_scope_is_verified_before_acceptance_and_replay(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        data = await seed(session)
        repository = SqlAlchemyErasureRepository(session)
        now = datetime.now(UTC)
        with pytest.raises(ErasureNotFound):
            await repository.accept(
                "practice_session",
                data["session"],
                data["owner"],
                "key",
                None,
                now,
                expected_project_id=uuid4(),
            )
        with pytest.raises(ErasureNotFound):
            await repository.accept(
                "project",
                data["project"],
                data["owner"],
                "key",
                "Synthetic project",
                now,
                expected_team_id=uuid4(),
            )
        await repository.accept(
            "project", data["project"], data["owner"], "key", "Synthetic project", now
        )
        with pytest.raises(ErasureNotFound):
            await repository.accept(
                "project",
                data["project"],
                data["owner"],
                "key",
                "Synthetic project",
                now,
                expected_team_id=uuid4(),
            )


@pytest.mark.anyio
async def test_partial_object_purge_resumes_only_unfinished_keys(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailSecondDelete(FakeObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []
            self.failed_once = False

        async def delete_object(self, key: str) -> None:
            if len(self.deleted) == 1 and not self.failed_once:
                self.failed_once = True
                raise StorageUnavailable("Synthetic private provider body")
            self.deleted.append(key)
            await super().delete_object(key)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FailSecondDelete(), Queue()
    async with db_session_factory() as session:
        data = await seed(session)
        extra = f"projects/{data['project']}/sessions/{data['session']}/attempts/1/qa.json"
        await storage.put_object(data["key"], b"synthetic", "video/mp4")
        await storage.put_object(extra, b"synthetic", "application/json")
        work = workflow(session, storage, queue, redis)
        request = await work.request(
            "project", data["project"], data["owner"], "key", "Synthetic project"
        )
        await work.run_due()
        await complete(work, queue.messages[0])
        await due_again(session, request.id)
        await work.run_due()
        failed = await work.get(request.id, data["owner"])
        objects = next(step for step in failed.steps if step.store == "objects")
        assert objects.status == "failed" and objects.deleted_objects == 1
        first_key = storage.deleted[0]
        await due_again(session, request.id)
        await work.run_due()
        completed = await work.get(request.id, data["owner"])
        assert completed.status == "completed"
        assert storage.deleted.count(first_key) == 1
        assert (
            next(step for step in completed.steps if step.store == "objects").deleted_objects == 2
        )
    await redis.aclose()


@pytest.mark.anyio
async def test_live_upload_grant_delays_final_purge_until_expiration(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.infrastructure.persistence.configurations import AssetVersionModel

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        data = await seed(session)
        version = await session.get(AssetVersionModel, data["version"])
        assert version is not None
        version.upload_expires_at = datetime.now(UTC) + timedelta(minutes=15)
        await session.commit()
        await storage.put_object(data["key"], b"synthetic", "video/mp4")
        work = workflow(session, storage, queue, redis)
        request = await work.request(
            "project", data["project"], data["owner"], "key", "Synthetic project"
        )
        await work.run_due()
        await complete(work, queue.messages[0])
        await due_again(session, request.id)
        await work.run_due()
        waiting = await work.get(request.id, data["owner"])
        assert waiting.status == "in_progress"
        assert next(step for step in waiting.steps if step.store == "objects").status == "pending"
        assert data["key"] in storage.objects
        row = await session.get(ErasureRequestModel, request.id)
        assert row is not None
        row.inventory = {
            **row.inventory,
            "upload_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
        await session.commit()
        await due_again(session, request.id)
        await work.run_due()
        assert (await work.get(request.id, data["owner"])).status == "completed"
        assert data["key"] not in storage.objects
    await redis.aclose()
