import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.asset import StorageUnavailable
from app.domain.erasure import Erasure, ErasureConflict, ErasureNotFound
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    AssetModel,
    ErasureRequestModel,
    PracticeSessionModel,
    ProjectErasureRequestModel,
    ProjectModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.sqlalchemy_erasure_repository import (
    SqlAlchemyErasureRepository,
)
from app.infrastructure.repositories.sqlalchemy_project_repository import (
    SqlAlchemyProjectRepository,
)
from tests.acceptance.test_erasure_workflow import Queue, complete, due_again, seed, workflow
from tests.support.fakes import FakeObjectStorage


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["pending", "failed"])
async def test_explicit_asset_delete_preserves_existing_retention_inventory(
    db_session_factory: async_sessionmaker[AsyncSession], status: str
) -> None:
    from app.infrastructure.persistence.configurations import AIJobModel, ErasureItemModel

    async with db_session_factory() as session:
        data = await seed(session, due=True)
        repository = SqlAlchemyErasureRepository(session)
        retained = await repository.accept(
            "asset", data["asset"], None, "retention", None, datetime.now(UTC), retention=True
        )
        row = await session.get(ErasureRequestModel, retained.id)
        assert row is not None
        row.status = status
        await session.commit()
        before = await repository.get(retained.id, data["owner"])
        job_ids = set(
            await session.scalars(
                select(AIJobModel.id).where(AIJobModel.erasure_request_id == retained.id)
            )
        )
        item_ids = set(
            await session.scalars(
                select(ErasureItemModel.id).where(ErasureItemModel.request_id == retained.id)
            )
        )
        for actor_id, parent_id, error in (
            (uuid4(), data["project"], ErasureNotFound),
            (data["owner"], uuid4(), ErasureNotFound),
            (data["owner"], data["project"], ErasureConflict),
        ):
            with pytest.raises(error):
                await repository.accept(
                    "asset",
                    data["asset"],
                    actor_id,
                    "delete",
                    "private-video.mp4",
                    datetime.now(UTC),
                    expected_project_id=parent_id,
                )
        after = await repository.get(retained.id, data["owner"])
        assert after == before
        assert job_ids == set(
            await session.scalars(
                select(AIJobModel.id).where(AIJobModel.erasure_request_id == retained.id)
            )
        )
        assert item_ids == set(
            await session.scalars(
                select(ErasureItemModel.id).where(ErasureItemModel.request_id == retained.id)
            )
        )


@pytest.mark.anyio
async def test_asset_download_refreshes_revocation_after_waiting_for_project_lock(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.application.services.asset_store import AssetStore
    from app.domain.asset import Asset, AssetNotFound
    from app.infrastructure.documents.document_verifier import DocumentVerifier
    from app.infrastructure.repositories.sqlalchemy_asset_repository import (
        SqlAlchemyAssetRepository,
    )

    read_asset = asyncio.Event()

    class ObservedRepository(SqlAlchemyAssetRepository):
        async def get_asset(self, asset_id: UUID) -> Asset | None:
            asset = await super().get_asset(asset_id)
            read_asset.set()
            return asset

    class ObservedStorage(FakeObjectStorage):
        signed_downloads = 0

        def generate_download_url(self, storage_key: str, ttl_seconds: int) -> tuple[str, datetime]:
            self.signed_downloads += 1
            return super().generate_download_url(storage_key, ttl_seconds)

    async with db_session_factory() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("Revocation lock ordering requires PostgreSQL")
        data = await seed(session)
        await session.execute(
            delete(SessionManifestModel).where(SessionManifestModel.session_id == data["session"])
        )
        await session.commit()

    storage = ObservedStorage()
    async with db_session_factory() as erasing, db_session_factory() as downloading:
        await erasing.scalar(
            select(ProjectModel).where(ProjectModel.id == data["project"]).with_for_update()
        )
        cached_asset = await downloading.get(AssetModel, data["asset"])
        assert cached_asset is not None and cached_asset.state == "verified"
        store = AssetStore(ObservedRepository(downloading), storage, DocumentVerifier())
        download = asyncio.create_task(store.create_download_intent(data["asset"], data["owner"]))
        await asyncio.wait_for(read_asset.wait(), timeout=5)
        try:
            await SqlAlchemyErasureRepository(erasing).accept(
                "asset",
                data["asset"],
                data["owner"],
                "delete",
                "private-video.mp4",
                datetime.now(UTC),
            )
            with pytest.raises(AssetNotFound):
                await asyncio.wait_for(download, timeout=5)
        finally:
            if not download.done():
                download.cancel()
                await asyncio.gather(download, return_exceptions=True)
        assert cached_asset.state == "deleting"
        assert storage.signed_downloads == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "confirmation",
    [None, "", "Wrong name", "synthetic project", " Synthetic project", "Synthetic project "],
)
async def test_project_erasure_rejects_invalid_confirmation_without_revoking_access(
    db_session_factory: async_sessionmaker[AsyncSession], confirmation: str | None
) -> None:
    async with db_session_factory() as session:
        data = await seed(session)
        repository = SqlAlchemyErasureRepository(session)
        now = datetime.now(UTC)
        with pytest.raises(ErasureConflict, match="confirmation_required"):
            await repository.accept(
                "project", data["project"], data["owner"], "key", confirmation, now
            )
        assert await session.scalar(select(ErasureRequestModel)) is None
        assert await SqlAlchemyProjectRepository(session).get_by_id(data["project"]) is not None
        practice_session = await session.get(PracticeSessionModel, data["session"])
        assert practice_session is not None and practice_session.status == SessionStatus.READY
        asset = await session.get(AssetModel, data["asset"])
        assert asset is not None and asset.state == "verified"

        accepted = await repository.accept(
            "project", data["project"], data["owner"], "key", "Synthetic project", now
        )
        replay = await repository.accept(
            "project", data["project"], data["owner"], "key", "Synthetic project", now
        )
        assert replay.id == accepted.id


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


@pytest.mark.anyio
@pytest.mark.parametrize("changed_payload", [False, True])
async def test_concurrent_replay_remembers_later_keys(
    db_session_factory: async_sessionmaker[AsyncSession], changed_payload: bool
) -> None:
    async with db_session_factory() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("Concurrent replay requires PostgreSQL row locks")
        data = await seed(session)
        accepted = await SqlAlchemyErasureRepository(session).accept(
            "project",
            data["project"],
            data["owner"],
            "original",
            "Synthetic project",
            datetime.now(UTC),
        )

    async def replay(confirmation: str) -> Erasure:
        async with db_session_factory() as session:
            return await SqlAlchemyErasureRepository(session).accept(
                "project", data["project"], data["owner"], "later", confirmation, datetime.now(UTC)
            )

    results = await asyncio.gather(
        replay("Synthetic project"),
        replay("Changed payload" if changed_payload else "Synthetic project"),
        return_exceptions=True,
    )
    successes = [result for result in results if isinstance(result, Erasure)]
    conflicts = [result for result in results if isinstance(result, ErasureConflict)]
    assert len(successes) == (1 if changed_payload else 2)
    assert len(conflicts) == (1 if changed_payload else 0)
    assert all(result.id == accepted.id for result in successes)
    assert all(str(result) == "idempotency_conflict" for result in conflicts)


@pytest.mark.anyio
async def test_asset_erasure_105_versions_outage_recovery_and_late_upload_grant(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.infrastructure.persistence.configurations import (
        AssetUploadIdempotencyModel,
        AssetVersionModel,
        ErasureItemModel,
    )

    class FailAfterSixtyDeletes(FakeObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []
            self.failed_once = False

        async def delete_object(self, key: str) -> None:
            if len(self.deleted) == 60 and not self.failed_once:
                self.failed_once = True
                raise StorageUnavailable("Simulated storage outage at 60")
            self.deleted.append(key)
            await super().delete_object(key)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FailAfterSixtyDeletes(), Queue()
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        data = await seed(session)
        asset_id = uuid4()
        session.add(
            AssetModel(
                id=asset_id,
                project_id=data["project"],
                kind="supporting_document",
                state="verified",
                file_name="multi-page-deck.pdf",
                created_by=data["owner"],
                created_at=now - timedelta(hours=2),
            )
        )
        await session.flush()
        version_ids: list[str] = []
        first_vid = uuid4()
        for idx in range(1, 106):
            vid = first_vid if idx == 1 else uuid4()
            version_ids.append(str(vid))
            v_key = f"teams/{data['team']}/projects/{data['project']}/assets/{asset_id}/{vid}.pdf"
            await storage.put_object(v_key, f"page-{idx}".encode(), "application/pdf")
            session.add(
                AssetVersionModel(
                    id=vid,
                    asset_id=asset_id,
                    version_number=idx,
                    state="pending_upload" if idx == 105 else "verified",
                    storage_key=v_key,
                    file_name="multi-page-deck.pdf",
                    declared_media_type="application/pdf",
                    declared_size_bytes=16,
                    created_by=data["owner"],
                    created_at=now - timedelta(hours=1),
                    upload_expires_at=(
                        now + timedelta(minutes=15) if idx == 105 else now - timedelta(minutes=5)
                    ),
                )
            )
        await session.flush()
        session.add(
            AssetUploadIdempotencyModel(
                id=uuid4(),
                user_id=data["owner"],
                project_id=data["project"],
                operation="create",
                key="idem-105",
                request_hash="hash-105",
                asset_id=asset_id,
                version_id=first_vid,
                created_at=now - timedelta(hours=1),
            )
        )
        await session.commit()

    late_team_key = f"teams/{data['team']}/projects/{data['project']}/assets/{asset_id}/late.pdf"
    late_proj_key = f"projects/{data['project']}/assets/{asset_id}/legacy-late.pdf"

    async with db_session_factory() as session:
        repo = SqlAlchemyErasureRepository(session)
        with pytest.raises(ErasureConflict, match="confirmation_required"):
            await repo.accept(
                "asset", asset_id, data["owner"], "key-105", "wrong.pdf", datetime.now(UTC)
            )
        request = await repo.accept(
            "asset", asset_id, data["owner"], "key-105", "multi-page-deck.pdf", datetime.now(UTC)
        )
        assert len(request.inventory["version_ids"]) == 105
        items = (
            await session.scalars(
                select(ErasureItemModel).where(ErasureItemModel.request_id == request.id)
            )
        ).all()
        assert len(items) == 105

    async with db_session_factory() as session:
        work = workflow(session, storage, queue, redis)
        await work.run_due()
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg.payload.scope == "asset"
        assert msg.payload.retention_only is False
        assert set(msg.payload.asset_version_ids) == set(version_ids)
        await complete(work, msg)

    async with db_session_factory() as session:
        work = workflow(session, storage, queue, redis)
        await due_again(session, request.id)
        await work.run_due()
        waiting = await work.get(request.id, data["owner"])
        assert waiting.status == "in_progress"
        assert next(s for s in waiting.steps if s.store == "objects").status == "pending"
        assert len(storage.deleted) == 0
        await storage.put_object(late_team_key, b"late-1", "application/pdf")
        await storage.put_object(late_proj_key, b"late-2", "application/pdf")

        row = await session.get(ErasureRequestModel, request.id)
        assert row is not None
        row.inventory = {
            **row.inventory,
            "upload_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
        await session.commit()

    async with db_session_factory() as session:
        work = workflow(session, storage, queue, redis)
        await due_again(session, request.id)
        await work.run_due()
        failed = await work.get(request.id, data["owner"])
        assert failed.status == "failed"
        obj_step = next(s for s in failed.steps if s.store == "objects")
        assert obj_step.status == "failed"
        assert obj_step.failure_code == "objects_unavailable"
        assert obj_step.deleted_objects == 60
        assert await session.get(AssetModel, asset_id) is not None
        pending_items = (
            await session.scalars(
                select(ErasureItemModel).where(
                    ErasureItemModel.request_id == request.id,
                    ErasureItemModel.status != "completed",
                )
            )
        ).all()
        assert len(pending_items) == 47

    async with db_session_factory() as session:
        work = workflow(session, storage, queue, redis)
        await due_again(session, request.id)
        await work.run_due()
        done = await work.get(request.id, data["owner"])
        assert done.status == "completed"
        obj_step = next(s for s in done.steps if s.store == "objects")
        assert obj_step.status == "completed"
        assert obj_step.deleted_objects == 107
        assert len(storage.deleted) == 107
        assert len(set(storage.deleted)) == 107
        assert late_team_key not in storage.objects
        assert late_proj_key not in storage.objects
        assert await session.get(AssetModel, asset_id) is None
        assert (
            await session.scalars(
                select(AssetVersionModel).where(AssetVersionModel.asset_id == asset_id)
            )
        ).all() == []

    await redis.aclose()


@pytest.mark.anyio
async def test_asset_erasure_blocks_immutable_links_and_preserves_session_a_drafts(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
    from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
    from app.domain.session_workflow.enums.qa import (
        AnswerStatus,
        QARoundState,
        QuestionKind,
        QuestionState,
    )
    from app.infrastructure.persistence.configurations import AssetVersionModel
    from app.infrastructure.persistence.configurations.session_workflow import (
        AIJobModel,
        AnalysisAttemptModel,
        AnswerModel,
        EvaluationModel,
        QARoundModel,
        QuestionModel,
        ReportExportModel,
        ReportModel,
        SessionManifestDocumentModel,
    )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        data = await seed(session)
        repo = SqlAlchemyErasureRepository(session)

        # 1. Non-DRAFT session (SessionStatus.READY from seed) blocks asset deletion
        with pytest.raises(ErasureConflict, match="immutable_manifest_requires_asset"):
            await repo.accept(
                "asset", data["asset"], data["owner"], "k-ready", "private-video.mp4", now
            )

        # Helper to create a fresh asset + version
        async def make_asset(kind: str, name: str) -> tuple[UUID, UUID]:
            aid, vid = uuid4(), uuid4()
            key = f"teams/{data['team']}/projects/{data['project']}/assets/{aid}/{vid}.bin"
            await storage.put_object(key, b"data", "application/octet-stream")
            session.add(
                AssetModel(
                    id=aid,
                    project_id=data["project"],
                    kind=kind,
                    state="verified",
                    file_name=name,
                    current_version_id=vid,
                    created_by=data["owner"],
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                AssetVersionModel(
                    id=vid,
                    asset_id=aid,
                    version_number=1,
                    state="verified",
                    storage_key=key,
                    file_name=name,
                    declared_media_type="application/pdf",
                    declared_size_bytes=4,
                    created_by=data["owner"],
                    created_at=now,
                    upload_expires_at=now - timedelta(minutes=1),
                )
            )
            await session.flush()
            return aid, vid

        # 2. Frozen manifest legacy document_version_id and SessionManifestDocumentModel
        doc_legacy_asset, doc_legacy_ver = await make_asset("supporting_document", "legacy.pdf")
        doc_join_asset, doc_join_ver = await make_asset("supporting_document", "join.pdf")
        audio_asset, audio_ver = await make_asset("answer_audio", "ans.webm")
        export_asset, export_ver = await make_asset("report_pdf", "exp.pdf")

        frozen_session_id = uuid4()
        session.add(
            PracticeSessionModel(
                id=frozen_session_id,
                project_id=data["project"],
                created_by=data["owner"],
                status=SessionStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        frozen_manifest = SessionManifestModel(
            id=uuid4(),
            session_id=frozen_session_id,
            presentation_version_id=data["version"],
            document_version_id=doc_legacy_ver,
            rubric_id="startup_pitch",
            rubric_version=1,
            frozen_at=now,
        )
        session.add(frozen_manifest)
        await session.flush()
        session.add(
            SessionManifestDocumentModel(
                id=uuid4(),
                manifest_id=frozen_manifest.id,
                document_version_id=doc_join_ver,
                created_at=now,
            )
        )
        attempt = AnalysisAttemptModel(
            id=uuid4(),
            session_id=frozen_session_id,
            manifest_id=frozen_manifest.id,
            attempt_number=1,
            status=AnalysisAttemptStatus.COMPLETED,
            created_at=now,
            version=1,
        )
        session.add(attempt)
        await session.flush()
        qa_round = QARoundModel(
            id=uuid4(),
            practice_session_id=frozen_session_id,
            analysis_attempt_id=attempt.id,
            state=QARoundState.COMPLETED,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(qa_round)
        await session.flush()
        question = QuestionModel(
            id=uuid4(),
            qa_round_id=qa_round.id,
            practice_session_id=frozen_session_id,
            kind=QuestionKind.PRIMARY,
            position=1,
            text="Q?",
            reason="R",
            rubric_dimension="clarity",
            evidence_ids=[],
            state=QuestionState.ANSWERED,
            created_at=now,
        )
        session.add(question)
        await session.flush()
        session.add(
            AnswerModel(
                id=uuid4(),
                qa_round_id=qa_round.id,
                question_id=question.id,
                answered_by=data["owner"],
                status=AnswerStatus.SUBMITTED,
                audio_asset_version_id=audio_ver,
                created_at=now,
                updated_at=now,
            )
        )
        eval_model = EvaluationModel(
            id=uuid4(),
            practice_session_id=frozen_session_id,
            analysis_attempt_id=attempt.id,
            qa_round_id=qa_round.id,
            rubric_id="startup_pitch",
            rubric_version=1,
            overall_score=80.0,
            payload={},
            created_at=now,
        )
        session.add(eval_model)
        await session.flush()
        rep_model = ReportModel(
            id=uuid4(),
            practice_session_id=frozen_session_id,
            evaluation_id=eval_model.id,
            title="Rep",
            executive_summary="Sum",
            overall_score=80.0,
            payload={},
            created_at=now,
            updated_at=now,
        )
        session.add(rep_model)
        await session.flush()
        session.add(
            ReportExportModel(
                id=uuid4(),
                report_id=rep_model.id,
                practice_session_id=frozen_session_id,
                format="pdf",
                status="completed",
                asset_version_id=export_ver,
                created_at=now,
            )
        )
        await session.commit()

        for blocked_aid, blocked_name in [
            (doc_legacy_asset, "legacy.pdf"),
            (doc_join_asset, "join.pdf"),
            (audio_asset, "ans.webm"),
            (export_asset, "exp.pdf"),
        ]:
            with pytest.raises(ErasureConflict, match="immutable_manifest_requires_asset"):
                await repo.accept(
                    "asset", blocked_aid, data["owner"], f"k-{blocked_name}", blocked_name, now
                )

        # 3. Session-A: unfrozen DRAFT optional doc detached, unfrozen DRAFT presentation cascaded
        draft_target_asset, draft_target_ver = await make_asset("presentation_video", "draft.mp4")
        frozen_manifest.presentation_version_id = draft_target_ver
        frozen_manifest.frozen_at = None
        surviving_draft_id = uuid4()
        cascaded_draft_id = uuid4()
        session.add_all(
            [
                PracticeSessionModel(
                    id=surviving_draft_id,
                    project_id=data["project"],
                    created_by=data["owner"],
                    status=SessionStatus.DRAFT,
                    created_at=now,
                    updated_at=now,
                ),
                PracticeSessionModel(
                    id=cascaded_draft_id,
                    project_id=data["project"],
                    created_by=data["owner"],
                    status=SessionStatus.DRAFT,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.flush()
        surviving_manifest = SessionManifestModel(
            id=uuid4(),
            session_id=surviving_draft_id,
            presentation_version_id=data["version"],
            document_version_id=draft_target_ver,
            rubric_id="startup_pitch",
            rubric_version=1,
            frozen_at=None,
        )
        cascaded_manifest = SessionManifestModel(
            id=uuid4(),
            session_id=cascaded_draft_id,
            presentation_version_id=draft_target_ver,
            rubric_id="startup_pitch",
            rubric_version=1,
            frozen_at=None,
        )
        session.add_all([surviving_manifest, cascaded_manifest])
        await session.flush()
        session.add(
            SessionManifestDocumentModel(
                id=uuid4(),
                manifest_id=surviving_manifest.id,
                document_version_id=draft_target_ver,
                created_at=now,
            )
        )
        draft_attempt = AnalysisAttemptModel(
            id=uuid4(),
            session_id=cascaded_draft_id,
            manifest_id=cascaded_manifest.id,
            attempt_number=1,
            status=AnalysisAttemptStatus.QUEUED,
            created_at=now,
            version=1,
        )
        session.add(draft_attempt)
        await session.flush()
        draft_job_id = uuid4()
        session.add(
            AIJobModel(
                id=draft_job_id,
                attempt_id=draft_attempt.id,
                practice_session_id=cascaded_draft_id,
                analysis_attempt=1,
                job_type="analyze_session",
                status=AnalysisJobStatus.PENDING,
                correlation_id=uuid4(),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

        cascaded_prefix_key = (
            f"projects/{data['project']}/sessions/{cascaded_draft_id}/draft-artifact.json"
        )
        await storage.put_object(cascaded_prefix_key, b"{}", "application/json")

        erasure = await repo.accept(
            "asset", draft_target_asset, data["owner"], "k-draft-ok", "draft.mp4", now
        )
        assert set(erasure.inventory["session_ids"]) == {
            str(cascaded_draft_id),
            str(frozen_session_id),
        }
        assert set(erasure.inventory["asset_ids"]) == {
            str(draft_target_asset),
            str(audio_asset),
            str(export_asset),
        }
        assert erasure.inventory["job_ids"] == [str(draft_job_id)]
        assert (
            f"projects/{data['project']}/sessions/{cascaded_draft_id}/"
            in erasure.inventory["object_prefixes"]
        )

        # Surviving draft session still active, optional doc refs detached immediately
        surv_session = await session.get(
            PracticeSessionModel, surviving_draft_id, populate_existing=True
        )
        assert surv_session is not None and surv_session.access_revoked_at is None
        surv_man = await session.get(
            SessionManifestModel, surviving_manifest.id, populate_existing=True
        )
        assert surv_man is not None and surv_man.document_version_id is None
        assert (
            await session.scalars(
                select(SessionManifestDocumentModel).where(
                    SessionManifestDocumentModel.manifest_id == surviving_manifest.id
                )
            )
        ).all() == []

        # Cascaded draft session is revoked immediately, then purged after workflow finishes
        casc_session = await session.get(
            PracticeSessionModel, cascaded_draft_id, populate_existing=True
        )
        assert casc_session is not None and casc_session.access_revoked_at is not None

        work = workflow(session, storage, queue, redis)
        await work.run_due()
        await complete(work, queue.messages[0])
        await due_again(session, erasure.id)
        await work.run_due()
        assert (await work.get(erasure.id, data["owner"])).status == "completed"
        assert (
            await session.get(PracticeSessionModel, cascaded_draft_id, populate_existing=True)
            is None
        )
        assert (
            await session.get(PracticeSessionModel, surviving_draft_id, populate_existing=True)
            is not None
        )
        assert cascaded_prefix_key not in storage.objects
        for owned_asset, owned_version in ((audio_asset, audio_ver), (export_asset, export_ver)):
            assert await session.get(AssetModel, owned_asset, populate_existing=True) is None
            assert (
                f"teams/{data['team']}/projects/{data['project']}/assets/{owned_asset}/{owned_version}.bin"
                not in storage.objects
            )
        for document_asset, document_version in (
            (doc_legacy_asset, doc_legacy_ver),
            (doc_join_asset, doc_join_ver),
        ):
            assert await session.get(AssetModel, document_asset, populate_existing=True) is not None
            assert (
                f"teams/{data['team']}/projects/{data['project']}/assets/{document_asset}/{document_version}.bin"
                in storage.objects
            )

        # 4. Prior retention erasure does not silently satisfy explicit user delete
        ret_asset, _ = await make_asset("presentation_video", "ret-due.mp4")
        ret_model = await session.get(AssetModel, ret_asset)
        assert ret_model is not None
        ret_model.retention_expires_at = now - timedelta(seconds=1)
        await session.commit()
        ret_req = await repo.accept(
            "asset", ret_asset, None, f"retention:{ret_asset}", None, now, retention=True
        )
        await work.run_due()
        await complete(work, queue.messages[-1])
        await due_again(session, ret_req.id)
        await work.run_due()
        assert (await work.get(ret_req.id, data["owner"])).status == "completed"
        assert await session.get(AssetModel, ret_asset, populate_existing=True) is not None

        with pytest.raises(ErasureConflict, match="retention_erasure_exists"):
            await repo.accept(
                "asset", ret_asset, data["owner"], "k-explicit-after-ret", "ret-due.mp4", now
            )
        assert (await work.get(ret_req.id, data["owner"])).status == "completed"
        assert (
            await session.scalar(
                select(AIJobModel.id).where(AIJobModel.erasure_request_id == ret_req.id)
            )
            is not None
        )
        assert await session.get(AssetModel, ret_asset, populate_existing=True) is not None

    await redis.aclose()


@pytest.mark.anyio
async def test_abandoned_cleanup_fence_and_replacement_race_protection(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.domain.asset import AssetNotFound, AssetVersion
    from app.domain.idempotency import AssetUploadIdempotency
    from app.infrastructure.persistence.configurations import AssetVersionModel
    from app.infrastructure.repositories.sqlalchemy_asset_repository import (
        SqlAlchemyAssetRepository,
    )

    now = datetime.now(UTC)
    old_time = now - timedelta(days=2)

    async with db_session_factory() as session:
        data = await seed(session)
        asset_id, ver1_id, ver2_id = uuid4(), uuid4(), uuid4()
        session.add(
            AssetModel(
                id=asset_id,
                project_id=data["project"],
                kind="supporting_document",
                state="verified",
                file_name="fenced.pdf",
                current_version_id=ver2_id,
                created_by=data["owner"],
                created_at=old_time,
            )
        )
        await session.flush()
        session.add_all(
            [
                AssetVersionModel(
                    id=ver1_id,
                    asset_id=asset_id,
                    version_number=1,
                    state="pending_upload",
                    storage_key=f"teams/{data['team']}/projects/{data['project']}/assets/{asset_id}/{ver1_id}.pdf",
                    file_name="fenced.pdf",
                    declared_media_type="application/pdf",
                    declared_size_bytes=100,
                    created_by=data["owner"],
                    created_at=old_time,
                    upload_expires_at=old_time,
                    cleanup_next_attempt_at=None,
                ),
                AssetVersionModel(
                    id=ver2_id,
                    asset_id=asset_id,
                    version_number=2,
                    state="verified",
                    storage_key=f"teams/{data['team']}/projects/{data['project']}/assets/{asset_id}/{ver2_id}.pdf",
                    file_name="fenced.pdf",
                    declared_media_type="application/pdf",
                    declared_size_bytes=100,
                    created_by=data["owner"],
                    created_at=old_time,
                    upload_expires_at=old_time,
                    cleanup_next_attempt_at=None,
                ),
            ]
        )
        await session.commit()

        asset_repo = SqlAlchemyAssetRepository(session)
        # Simulate cleanup worker claiming ver1 right before erasure acceptance
        claimed = await asset_repo.claim_version_for_cleanup(
            asset_id=asset_id,
            version_id=ver1_id,
            now=now,
            lease_seconds=300,
            tombstone_delay_seconds=86400,
            cutoff_created_at=now - timedelta(seconds=86400),
            cutoff_expires_at=now,
        )
        assert claimed is not None

        erasure_repo = SqlAlchemyErasureRepository(session)
        await erasure_repo.accept("asset", asset_id, data["owner"], "fence-key", "fenced.pdf", now)

        # 1. Candidate query excludes deleting asset versions
        candidates = await asset_repo.find_cleanup_candidate_versions(
            cutoff_created_at=now - timedelta(seconds=86400),
            cutoff_expires_at=now,
            now=now + timedelta(hours=1),
            limit=50,
        )
        assert all(aid != asset_id for aid, _ in candidates)

        # 2. Claim returns None for deleting asset
        assert (
            await asset_repo.claim_version_for_cleanup(
                asset_id=asset_id,
                version_id=ver1_id,
                now=now + timedelta(hours=1),
                lease_seconds=300,
                tombstone_delay_seconds=86400,
                cutoff_created_at=now - timedelta(seconds=86400),
                cutoff_expires_at=now,
            )
            is None
        )

        # 3. Stale in-flight finalize_version_cleanup or record_cleanup_failure cannot resurrect
        await asset_repo.record_cleanup_failure(
            asset_id=asset_id,
            version_id=ver1_id,
            next_attempt_at=now + timedelta(minutes=10),
        )
        await asset_repo.finalize_version_cleanup(
            asset_id=asset_id,
            version_id=ver1_id,
            next_attempt_at=now + timedelta(days=1),
        )
        asset_row = await session.get(AssetModel, asset_id, populate_existing=True)
        ver1_row = await session.get(AssetVersionModel, ver1_id, populate_existing=True)
        assert asset_row is not None and asset_row.state == "deleting"
        assert ver1_row is not None and ver1_row.state == "deleting"

        # 4. Replacement version creation is blocked on deleting asset
        with pytest.raises(AssetNotFound):
            await asset_repo.save_replacement_version(
                asset_id,
                AssetVersion(
                    id=uuid4(),
                    asset_id=asset_id,
                    version_number=3,
                    state="pending_upload",
                    storage_key=f"teams/{data['team']}/projects/{data['project']}/assets/{asset_id}/v3.pdf",
                    file_name="v3.pdf",
                    declared_media_type="application/pdf",
                    declared_size_bytes=100,
                    created_by=data["owner"],
                    created_at=now,
                    upload_expires_at=now + timedelta(minutes=15),
                ),
                AssetUploadIdempotency(
                    user_id=data["owner"],
                    project_id=data["project"],
                    operation=f"version_upload:{asset_id.hex}",
                    key="race-v3",
                    request_hash="hash-v3",
                    asset_id=asset_id,
                    version_id=uuid4(),
                ),
            )
