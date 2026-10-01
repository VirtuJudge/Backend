from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ai_job_contracts import AIWorkerUpdate, EraseAIDataQueueMessage
from app.application.erasure_workflow import ErasureWorkflow
from app.application.ports.ai_queue import AIJobQueueAccepted, AIJobQueueEnvelope, AIJobQueuePort
from app.domain.erasure import ErasureConflict, ErasureForbidden, ErasureNotFound
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ErasureItemModel,
    ErasureRequestModel,
    PracticeSessionModel,
    ProjectModel,
    SessionManifestModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.redis.erasure import RedisErasureCache
from app.infrastructure.redis.session_notifications import RedisSessionNotifications
from app.infrastructure.repositories.session_workflow import SqlAlchemyPracticeSessionRepository
from app.infrastructure.repositories.sqlalchemy_erasure_repository import (
    SqlAlchemyErasureRepository,
)
from app.infrastructure.repositories.sqlalchemy_project_repository import (
    SqlAlchemyProjectRepository,
)
from tests.support.fakes import FakeObjectStorage


class Queue(AIJobQueuePort):
    def __init__(self) -> None:
        self.messages: list[EraseAIDataQueueMessage] = []
        self.cancelled: list[str] = []

    async def enqueue(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        assert isinstance(message, EraseAIDataQueueMessage)
        self.messages.append(message)
        return AIJobQueueAccepted(message.job_id, datetime.now(UTC))

    async def cancel_jobs(self, ids: list[str]) -> int:
        self.cancelled.extend(ids)
        return len(ids)


async def seed(session: AsyncSession, *, due: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC)
    owner, team, project, asset, version, session_id = [uuid4() for _ in range(6)]
    session.add_all(
        [
            UserModel(id=owner, issuer="test", subject=str(owner), created_at=now),
            TeamModel(id=team, name="Synthetic team", created_at=now),
        ]
    )
    await session.flush()
    session.add(
        TeamMemberModel(id=uuid4(), team_id=team, user_id=owner, role="owner", joined_at=now)
    )
    session.add(ProjectModel(id=project, team_id=team, name="Synthetic project", created_at=now))
    await session.flush()
    session.add(
        AssetModel(
            id=asset,
            project_id=project,
            kind="presentation_video",
            state="verified",
            file_name="private-video.mp4",
            current_version_id=version,
            created_by=owner,
            created_at=now,
            retention_expires_at=now - timedelta(seconds=1) if due else now + timedelta(days=30),
        )
    )
    await session.flush()
    key = f"teams/{team}/projects/{project}/assets/{asset}/{version}.mp4"
    session.add(
        AssetVersionModel(
            id=version,
            asset_id=asset,
            version_number=1,
            state="verified",
            storage_key=key,
            file_name="private-video.mp4",
            declared_media_type="video/mp4",
            declared_size_bytes=1,
            created_by=owner,
            created_at=now,
            upload_expires_at=now,
        )
    )
    session.add(
        PracticeSessionModel(
            id=session_id,
            project_id=project,
            created_by=owner,
            status=SessionStatus.READY,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    session.add(
        SessionManifestModel(
            id=uuid4(),
            session_id=session_id,
            presentation_version_id=version,
            rubric_id="startup_pitch",
            rubric_version=1,
        )
    )
    pdf, pdf_version = uuid4(), uuid4()
    pdf_key = f"teams/{team}/projects/{project}/assets/{pdf}/{pdf_version}.pdf"
    session.add(
        AssetModel(
            id=pdf,
            project_id=project,
            kind="report_pdf",
            state="verified",
            file_name="report.pdf",
            current_version_id=pdf_version,
            created_by=owner,
            created_at=now,
        )
    )
    await session.flush()
    session.add(
        AssetVersionModel(
            id=pdf_version,
            asset_id=pdf,
            state="verified",
            storage_key=pdf_key,
            file_name="report.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1,
            created_by=owner,
            created_at=now,
            upload_expires_at=now,
        )
    )
    await session.commit()
    return dict(
        owner=owner,
        team=team,
        project=project,
        asset=asset,
        version=version,
        session=session_id,
        key=key,
        pdf_key=pdf_key,
    )


async def due_again(session: AsyncSession, request_id: UUID) -> None:
    await session.execute(
        update(ErasureRequestModel)
        .where(ErasureRequestModel.id == request_id)
        .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


def workflow(session: AsyncSession, storage: Any, queue: Queue, redis: Any) -> ErasureWorkflow:
    return ErasureWorkflow(
        SqlAlchemyErasureRepository(session),
        storage,
        queue,
        queue,
        RedisErasureCache(redis),
        RedisSessionNotifications(redis, max_events=100, retention_seconds=86400),
    )


async def complete(work: ErasureWorkflow, message: EraseAIDataQueueMessage) -> None:
    await work.record_update(
        UUID(message.job_id),
        AIWorkerUpdate.model_validate(
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC),
                "trace_id": message.trace_id,
                "payload": {"pipeline_version": "test"},
            }
        ),
    )
    update_payload = AIWorkerUpdate.model_validate(
        {
            "schema_version": 1,
            "sequence": 2,
            "status": "completed",
            "occurred_at": datetime.now(UTC),
            "trace_id": message.trace_id,
            "payload": {
                "erasure_request_id": message.payload.erasure_request_id,
                "deleted_records": 3,
                "deleted_objects": 2,
            },
        }
    )
    await work.record_update(UUID(message.job_id), update_payload)
    await work.record_update(UUID(message.job_id), update_payload)


@pytest.mark.anyio
async def test_project_erasure_revokes_then_purges_all_stores_and_keeps_tombstone(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage = FakeObjectStorage()
    queue = Queue()
    async with db_session_factory() as session:
        target, unrelated = await seed(session), await seed(session)
        for data in (target, unrelated):
            await storage.put_object(data["key"], b"private media", "video/mp4")
            await storage.put_object(data["pdf_key"], b"private report", "application/pdf")
            await redis.set(f"session-events:{{{data['session']}}}:sequence", "1")
        work = workflow(session, storage, queue, redis)
        request = await work.request(
            "project", target["project"], target["owner"], "delete", "Synthetic project"
        )
        assert request.deadline_at - request.requested_at == timedelta(hours=24)
        assert await SqlAlchemyProjectRepository(session).get_by_id(target["project"]) is None
        assert (
            await SqlAlchemyPracticeSessionRepository(session).get_by_id(target["session"]) is None
        )
        assert (
            await work.request(
                "project", target["project"], target["owner"], "delete", "Synthetic project"
            )
        ).id == request.id
        with pytest.raises(ErasureConflict):
            await work.request("project", target["project"], target["owner"], "delete", "wrong")
        with pytest.raises(ErasureNotFound):
            await work.get(request.id, unrelated["owner"])
        await work.run_due()
        message = queue.messages[0]
        assert message.practice_session_id is None
        assert message.payload.practice_session_ids == [str(target["session"])]
        await complete(work, message)
        await due_again(session, request.id)
        await work.run_due()
        final = await work.get(request.id, target["owner"])
        assert final.status == "completed"
        assert all(step.status == "completed" for step in final.steps)
        assert next(step for step in final.steps if step.store == "ai_data").deleted_records == 3
        assert (
            next(step for step in final.steps if step.store == "generated_pdfs").deleted_objects
            == 1
        )
        assert await session.get(ProjectModel, target["project"]) is None
        assert await session.get(AssetModel, target["asset"]) is None
        assert final.inventory == {}
        assert not (await session.scalars(select(ErasureItemModel))).all()
        assert await redis.get(f"session-events:{{{target['session']}}}:sequence") is None
        assert await redis.get(f"session-events:{{{unrelated['session']}}}:sequence") == "1"
        assert await session.get(ProjectModel, unrelated["project"]) is not None
        assert target["key"] not in storage.objects
        assert unrelated["key"] in storage.objects
        assert unrelated["pdf_key"] in storage.objects
        assert (
            await work.request(
                "project", target["project"], target["owner"], "replay", "Synthetic project"
            )
        ).id == request.id
    await redis.aclose()


@pytest.mark.anyio
async def test_raw_media_retention_preserves_sessions_and_pdfs(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        expired, fresh = await seed(session, due=True), await seed(session)
        for data in (expired, fresh):
            await storage.put_object(data["key"], b"media", "video/mp4")
            await storage.put_object(data["pdf_key"], b"pdf", "application/pdf")
        work = workflow(session, storage, queue, redis)
        assert await work.sweep_retention() == 1
        await work.run_due()
        message = queue.messages[0]
        assert message.payload.retention_only
        await complete(work, message)
        await due_again(session, UUID(message.payload.erasure_request_id))
        await work.run_due()
        asset = await session.get(AssetModel, expired["asset"], populate_existing=True)
        assert asset is not None and asset.state == "deleted"
        assert (
            await SqlAlchemyPracticeSessionRepository(session).get_by_id(expired["session"])
            is not None
        )
        assert expired["key"] not in storage.objects
        assert expired["pdf_key"] in storage.objects
        assert fresh["key"] in storage.objects
        assert await work.sweep_retention() == 0
    await redis.aclose()


@pytest.mark.anyio
async def test_owner_permission_and_storage_failure_retry(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        target = await seed(session)
        work = workflow(session, storage, queue, redis)
        await session.execute(
            update(TeamMemberModel)
            .where(TeamMemberModel.user_id == target["owner"])
            .values(role="member")
        )
        await session.commit()
        with pytest.raises(ErasureForbidden):
            await work.request(
                "project", target["project"], target["owner"], "key", "Synthetic project"
            )
        await session.execute(
            update(TeamMemberModel)
            .where(TeamMemberModel.user_id == target["owner"])
            .values(role="owner")
        )
        await session.commit()
        request = await work.request(
            "project", target["project"], target["owner"], "key", "Synthetic project"
        )
        await work.run_due()
        await complete(work, queue.messages[0])
        storage.transient_failure = True
        await due_again(session, request.id)
        await work.run_due()
        failed = await work.get(request.id, target["owner"])
        assert failed.status == "failed"
        assert (
            next(step for step in failed.steps if step.store == "objects").failure_code
            == "objects_unavailable"
        )
        assert await session.get(ProjectModel, target["project"]) is not None
        storage.transient_failure = False
        await due_again(session, request.id)
        await work.run_due()
        assert (await work.get(request.id, target["owner"])).status == "completed"
    await redis.aclose()
