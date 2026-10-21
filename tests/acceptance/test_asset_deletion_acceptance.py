from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.erasure_workflow import ErasureWorkflow
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.user import User
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ErasureItemModel,
    PracticeSessionModel,
    ProjectModel,
    SessionManifestModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.redis.erasure import RedisErasureCache
from app.infrastructure.redis.session_notifications import RedisSessionNotifications
from app.infrastructure.repositories.sqlalchemy_erasure_repository import (
    SqlAlchemyErasureRepository,
)
from app.main import asset_store_factory, create_app
from app.settings import Settings
from tests.acceptance.test_erasure_workflow import Queue, complete, due_again
from tests.support.constants import MEMBER_ID, NOW, OUTSIDER_ID, PROJECT_ID, TEAM_ID
from tests.support.documents import make_pdf
from tests.support.fakes import FakeObjectStorage, FakeTokenVerifier, FakeUserService

OWNER_ID = UUID("aaaa0000-0000-0000-0000-000000000099")


@pytest.fixture
def owner_user() -> User:
    return User(
        id=OWNER_ID,
        display_name="Owner User",
        issuer="https://auth.example",
        subject="owner-sub",
        email="owner@example.com",
        created_at=NOW,
    )


async def seed_hierarchy(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                UserModel(
                    id=OWNER_ID,
                    email="owner@example.com",
                    issuer="https://auth.example",
                    subject="owner-sub",
                    created_at=NOW,
                ),
                UserModel(
                    id=MEMBER_ID,
                    email="member@example.com",
                    issuer="https://auth.example",
                    subject="member-sub",
                    created_at=NOW,
                ),
                UserModel(
                    id=OUTSIDER_ID,
                    email="outsider@example.com",
                    issuer="https://auth.example",
                    subject="outsider-sub",
                    created_at=NOW,
                ),
                TeamModel(id=TEAM_ID, name="Test Team", created_at=NOW),
            ]
        )
        await session.flush()
        session.add_all(
            [
                TeamMemberModel(
                    id=uuid4(),
                    team_id=TEAM_ID,
                    user_id=OWNER_ID,
                    role="owner",
                    joined_at=NOW,
                ),
                TeamMemberModel(
                    id=uuid4(),
                    team_id=TEAM_ID,
                    user_id=MEMBER_ID,
                    role="member",
                    joined_at=NOW,
                ),
                ProjectModel(
                    id=PROJECT_ID,
                    team_id=TEAM_ID,
                    name="Test Project",
                    created_at=NOW,
                    version=1,
                ),
            ]
        )
        await session.commit()


def build_client(
    user: User,
    session_factory: async_sessionmaker[AsyncSession],
    storage: FakeObjectStorage,
    queue: Queue,
    redis: Any,
) -> AsyncClient:
    settings = Settings(
        app_env="test",
        oidc_issuer="https://auth.example",
        oidc_audience="test",
        oidc_jwks_url="https://auth.example/.well-known/jwks.json",
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
    )
    app = create_app(settings)
    app.state.token_verifier = FakeTokenVerifier(user)
    app.state.user_service_factory = lambda session: FakeUserService(user)
    app.state.object_storage = storage

    def store_factory(session: AsyncSession) -> Any:
        store = asset_store_factory(session, settings)
        store.storage = storage
        return store

    app.state.asset_store_factory = store_factory
    app.state.erasure_workflow_factory = lambda session: ErasureWorkflow(
        SqlAlchemyErasureRepository(session),
        storage,
        queue,
        queue,
        RedisErasureCache(redis),
        RedisSessionNotifications(redis, max_events=100, retention_seconds=86400),
    )

    async def override_session(request: Any = None) -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.state.session_dependency = override_session
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer fake-token"},
    )


@pytest.mark.anyio
async def test_asset_delete_requires_owner_confirmation_and_revokes_immediately(
    db_session_factory: async_sessionmaker[AsyncSession],
    owner_user: User,
    member_user: User,
) -> None:
    await seed_hierarchy(db_session_factory)
    storage = FakeObjectStorage()
    queue = Queue()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pdf_bytes = make_pdf()

    async with build_client(
        member_user, db_session_factory, storage, queue, redis
    ) as member_client:
        init_res = await member_client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "document.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": len(pdf_bytes),
            },
            headers={"Idempotency-Key": "test-del-1"},
        )
        assert init_res.status_code == 201
        asset_id = UUID(init_res.json()["asset_id"])
        version_id = UUID(init_res.json()["asset_version_id"])

        forbidden = await member_client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "member-del"},
            json={"confirmation": "document.pdf"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "forbidden"

    async with build_client(owner_user, db_session_factory, storage, queue, redis) as owner_client:
        for bad_conf in ["wrong.pdf", "DOCUMENT.PDF", " document.pdf", "document.pdf "]:
            mismatch = await owner_client.request(
                "DELETE",
                f"/api/v1/assets/{asset_id}",
                headers={"Idempotency-Key": f"bad-{bad_conf}"},
                json={"confirmation": bad_conf},
            )
            assert mismatch.status_code == 409
            assert mismatch.json()["code"] == "confirmation_required"

        still_there = await owner_client.get(f"/api/v1/assets/{asset_id}")
        assert still_there.status_code == 200

        del_res = await owner_client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "owner-del-1"},
            json={"confirmation": "document.pdf"},
        )
        assert del_res.status_code == 202
        erasure_id = del_res.json()["id"]
        assert del_res.json()["scope"] == "asset"
        assert del_res.json()["scope_id"] == str(asset_id)
        assert del_res.headers["Location"] == f"/api/v1/erasure-requests/{erasure_id}"

        assert (await owner_client.get(f"/api/v1/assets/{asset_id}")).status_code == 404
        assert (await owner_client.get(f"/api/v1/assets/{asset_id}/versions")).status_code == 404
        assert (
            await owner_client.get(f"/api/v1/assets/{asset_id}/versions/{version_id}")
        ).status_code == 404
        assert (
            await owner_client.post(f"/api/v1/assets/{asset_id}/download-intents")
        ).status_code == 404
        assert (
            await owner_client.post(
                f"/api/v1/assets/{asset_id}/versions/{version_id}/download-intents"
            )
        ).status_code == 404
        assert (
            await owner_client.post(
                f"/api/v1/assets/{asset_id}/versions/upload-intents",
                headers={"Idempotency-Key": "rep-after-del"},
                json={
                    "file_name": "v2.pdf",
                    "declared_media_type": "application/pdf",
                    "declared_size_bytes": 100,
                },
            )
        ).status_code == 404
        assert (
            await owner_client.post(
                f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
                headers={"Idempotency-Key": "comp-after-del"},
                json={"checksum": "sha256:" + "a" * 64, "size_bytes": len(pdf_bytes)},
            )
        ).status_code == 404

        listed = await owner_client.get(f"/api/v1/projects/{PROJECT_ID}/assets")
        assert listed.status_code == 200
        assert listed.json()["items"] == []

        conflict_replay = await owner_client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "owner-del-1"},
            json={"confirmation": "other.pdf"},
        )
        assert conflict_replay.status_code == 409
        assert conflict_replay.json()["code"] == "idempotency_conflict"

        same_replay = await owner_client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "owner-del-1"},
            json={"confirmation": "document.pdf"},
        )
        assert same_replay.status_code == 202
        assert same_replay.json()["id"] == erasure_id

    await redis.aclose()


@pytest.mark.anyio
async def test_asset_delete_via_project_scoped_route_and_replay_after_purge(
    db_session_factory: async_sessionmaker[AsyncSession],
    owner_user: User,
) -> None:
    await seed_hierarchy(db_session_factory)
    storage = FakeObjectStorage()
    queue = Queue()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async with build_client(owner_user, db_session_factory, storage, queue, redis) as client:
        init_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc2.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 500,
            },
            headers={"Idempotency-Key": "test-del-2"},
        )
        assert init_res.status_code == 201
        asset_id = UUID(init_res.json()["asset_id"])
        version_id = UUID(init_res.json()["asset_version_id"])

        wrong_parent = await client.request(
            "DELETE",
            f"/api/v1/projects/{uuid4()}/assets/{asset_id}",
            headers={"Idempotency-Key": "wrong-parent"},
            json={"confirmation": "doc2.pdf"},
        )
        assert wrong_parent.status_code == 404

        del_res = await client.request(
            "DELETE",
            f"/api/v1/projects/{PROJECT_ID}/assets/{asset_id}",
            headers={"Idempotency-Key": "del-project-route"},
            json={"confirmation": "doc2.pdf"},
        )
        assert del_res.status_code == 202
        erasure_id = UUID(del_res.json()["id"])

        async with db_session_factory() as session:
            ver = await session.get(AssetVersionModel, version_id)
            assert ver is not None
            await storage.put_object(ver.storage_key, b"pdf-data", "application/pdf")
            ver.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            from app.infrastructure.persistence.configurations import ErasureRequestModel

            req = await session.get(ErasureRequestModel, erasure_id)
            assert req is not None
            req.inventory = {
                **req.inventory,
                "upload_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
            await session.commit()

        async with db_session_factory() as session:
            work = ErasureWorkflow(
                SqlAlchemyErasureRepository(session),
                storage,
                queue,
                queue,
                RedisErasureCache(redis),
            )
            await work.run_due()
            await complete(work, queue.messages[0])
            await due_again(session, erasure_id)
            await work.run_due()
            assert await session.get(AssetModel, asset_id) is None

        replay_after_purge = await client.request(
            "DELETE",
            f"/api/v1/projects/{PROJECT_ID}/assets/{asset_id}",
            headers={"Idempotency-Key": "replay-after-purge"},
            json={"confirmation": "doc2.pdf"},
        )
        assert replay_after_purge.status_code == 202
        assert replay_after_purge.json()["id"] == str(erasure_id)
        assert replay_after_purge.json()["status"] == "completed"

    await redis.aclose()


@pytest.mark.anyio
async def test_asset_delete_forbidden_for_non_member_and_not_found(
    db_session_factory: async_sessionmaker[AsyncSession],
    owner_user: User,
    outsider_user: User,
) -> None:
    await seed_hierarchy(db_session_factory)
    storage = FakeObjectStorage()
    queue = Queue()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async with build_client(owner_user, db_session_factory, storage, queue, redis) as owner_client:
        init_res = await owner_client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc3.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 500,
            },
            headers={"Idempotency-Key": "test-del-3"},
        )
        asset_id = init_res.json()["asset_id"]

        not_found = await owner_client.request(
            "DELETE",
            f"/api/v1/assets/{uuid4()}",
            headers={"Idempotency-Key": "nf-1"},
            json={"confirmation": "doc3.pdf"},
        )
        assert not_found.status_code == 404
        assert not_found.json()["code"] == "not_found"

    async with build_client(
        outsider_user, db_session_factory, storage, queue, redis
    ) as outsider_client:
        del_res = await outsider_client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "outsider-del"},
            json={"confirmation": "doc3.pdf"},
        )
        assert del_res.status_code == 404
        assert del_res.json()["code"] == "not_found"

    await redis.aclose()


@pytest.mark.anyio
async def test_asset_delete_retains_inventory_on_storage_failure_and_purges_105_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
    owner_user: User,
) -> None:
    await seed_hierarchy(db_session_factory)
    storage = FakeObjectStorage()
    queue = Queue()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    now = datetime.now(UTC)
    asset_id = uuid4()

    async with db_session_factory() as session:
        session.add(
            AssetModel(
                id=asset_id,
                project_id=PROJECT_ID,
                kind="supporting_document",
                state="verified",
                file_name="deck-105.pdf",
                created_by=OWNER_ID,
                created_at=now - timedelta(hours=2),
            )
        )
        await session.flush()
        keys: list[str] = []
        for idx in range(1, 106):
            vid = uuid4()
            key = f"teams/{TEAM_ID}/projects/{PROJECT_ID}/assets/{asset_id}/{vid}.pdf"
            keys.append(key)
            await storage.put_object(key, f"v-{idx}".encode(), "application/pdf")
            session.add(
                AssetVersionModel(
                    id=vid,
                    asset_id=asset_id,
                    version_number=idx,
                    state="verified",
                    storage_key=key,
                    file_name="deck-105.pdf",
                    declared_media_type="application/pdf",
                    declared_size_bytes=10,
                    created_by=OWNER_ID,
                    created_at=now - timedelta(hours=2),
                    upload_expires_at=now - timedelta(hours=1),
                )
            )
        await session.commit()

    async with build_client(owner_user, db_session_factory, storage, queue, redis) as client:
        del_res = await client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "del-105"},
            json={"confirmation": "deck-105.pdf"},
        )
        assert del_res.status_code == 202
        erasure_id = UUID(del_res.json()["id"])

        async with db_session_factory() as session:
            work = ErasureWorkflow(
                SqlAlchemyErasureRepository(session),
                storage,
                queue,
                queue,
                RedisErasureCache(redis),
            )
            await work.run_due()
            assert len(queue.messages) == 1
            msg = queue.messages[0]
            assert msg.payload.scope == "asset"
            assert msg.payload.retention_only is False
            assert len(msg.payload.asset_version_ids) == 105
            await complete(work, msg)

            storage.transient_failure = True
            await due_again(session, erasure_id)
            await work.run_due()

        status_failed = await client.get(f"/api/v1/erasure-requests/{erasure_id}")
        assert status_failed.status_code == 200
        assert status_failed.json()["status"] == "failed"
        objects_step = next(s for s in status_failed.json()["steps"] if s["store"] == "objects")
        assert objects_step["failure_code"] == "objects_unavailable"

        async with db_session_factory() as session:
            assert await session.get(AssetModel, asset_id) is not None
            remaining_items = (
                await session.scalars(
                    select(ErasureItemModel).where(ErasureItemModel.request_id == erasure_id)
                )
            ).all()
            assert len(remaining_items) == 105

            storage.transient_failure = False
            work = ErasureWorkflow(
                SqlAlchemyErasureRepository(session),
                storage,
                queue,
                queue,
                RedisErasureCache(redis),
            )
            await due_again(session, erasure_id)
            await work.run_due()

        status_done = await client.get(f"/api/v1/erasure-requests/{erasure_id}")
        assert status_done.status_code == 200
        assert status_done.json()["status"] == "completed"
        assert storage.objects == {}

        async with db_session_factory() as session:
            assert await session.get(AssetModel, asset_id) is None
            assert (
                await session.scalars(
                    select(AssetVersionModel).where(AssetVersionModel.asset_id == asset_id)
                )
            ).all() == []

    await redis.aclose()


@pytest.mark.anyio
async def test_asset_delete_blocks_when_immutable_manifest_requires_asset(
    db_session_factory: async_sessionmaker[AsyncSession],
    owner_user: User,
) -> None:
    await seed_hierarchy(db_session_factory)
    storage = FakeObjectStorage()
    queue = Queue()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    now = datetime.now(UTC)
    asset_id, version_id, session_id = uuid4(), uuid4(), uuid4()

    async with db_session_factory() as session:
        session.add(
            AssetModel(
                id=asset_id,
                project_id=PROJECT_ID,
                kind="presentation_video",
                state="verified",
                file_name="pitch.mp4",
                current_version_id=version_id,
                created_by=OWNER_ID,
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            AssetVersionModel(
                id=version_id,
                asset_id=asset_id,
                version_number=1,
                state="verified",
                storage_key=f"teams/{TEAM_ID}/projects/{PROJECT_ID}/assets/{asset_id}/{version_id}.mp4",
                file_name="pitch.mp4",
                declared_media_type="video/mp4",
                declared_size_bytes=100,
                created_by=OWNER_ID,
                created_at=now,
                upload_expires_at=now,
            )
        )
        session.add(
            PracticeSessionModel(
                id=session_id,
                project_id=PROJECT_ID,
                created_by=OWNER_ID,
                status=SessionStatus.ANALYZING,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            SessionManifestModel(
                id=uuid4(),
                session_id=session_id,
                presentation_version_id=version_id,
                rubric_id="startup_pitch",
                rubric_version=1,
                frozen_at=now,
            )
        )
        await session.commit()

    async with build_client(owner_user, db_session_factory, storage, queue, redis) as client:
        res = await client.request(
            "DELETE",
            f"/api/v1/assets/{asset_id}",
            headers={"Idempotency-Key": "blocked-del"},
            json={"confirmation": "pitch.mp4"},
        )
        assert res.status_code == 409
        assert res.headers["content-type"] == "application/problem+json"
        assert (await client.get(f"/api/v1/assets/{asset_id}")).status_code == 200

    await redis.aclose()
