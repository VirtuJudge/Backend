import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.assetStore import AssetStore
from app.domain.asset import (
    Asset,
    AssetCompletionConflict,
    AssetIdempotencyConflict,
    AssetVersion,
    StorageUnavailable,
)
from app.domain.idempotency import AssetUploadIdempotency
from app.infrastructure.database import Base
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemyAssetRepository import SqlAlchemyAssetRepository
from app.settings import Settings
from app.main import create_app
from tests.test_asset_acceptance import (
    MEMBER_ID,
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    make_pdf,
)

NOW = datetime.now(UTC)


@pytest.fixture
async def db_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "asset_cleanup_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def seed_db(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    async with db_session_factory() as session:
        user_id = uuid4()
        team_id = uuid4()
        project_id = uuid4()

        session.add(
            UserModel(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                issuer="https://auth.example",
                subject=f"sub-{user_id.hex[:6]}",
                created_at=NOW,
            )
        )
        session.add(TeamModel(id=team_id, name="Test Team", created_at=NOW))
        session.add(
            TeamMemberModel(
                team_id=team_id,
                user_id=user_id,
                role="member",
                joined_at=NOW,
            )
        )
        session.add(
            ProjectModel(
                id=project_id,
                team_id=team_id,
                name="Test Project",
                created_at=NOW,
                version=1,
            )
        )
        await session.commit()
        return user_id, team_id, project_id


class RecordingStorage(FakeObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.deleted_keys: list[str] = []
        self.last_ttl_seconds: int | None = None
        self.fail_delete = False

    def generate_upload_url(
        self,
        storage_key: str,
        content_type: str,
        content_length: int,
        ttl_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        self.last_ttl_seconds = ttl_seconds
        return super().generate_upload_url(storage_key, content_type, content_length, ttl_seconds)

    async def delete_object(self, storage_key: str) -> None:
        if self.fail_delete:
            raise StorageUnavailable("Simulated storage failure during delete")
        self.deleted_keys.append(storage_key)
        self.objects.pop(storage_key, None)


@pytest.mark.anyio
async def test_replay_upload_intent_signs_remaining_ttl_without_renewal() -> None:
    repo = FakeAssetRepository()
    storage = RecordingStorage()
    store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )

    intent1 = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=1024,
        idempotency_key="key-ttl-1",
    )
    assert storage.last_ttl_seconds in (899, 900)

    # Advance time by 300 seconds on the existing version
    ver = repo.versions[intent1.asset_version_id]
    ver.upload_expires_at = datetime.now(UTC) + timedelta(seconds=600)

    # Replay should sign remaining 600s, not renew to 900s
    intent2 = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=1024,
        idempotency_key="key-ttl-1",
    )
    assert intent2.asset_version_id == intent1.asset_version_id
    assert storage.last_ttl_seconds is not None
    assert 595 <= storage.last_ttl_seconds <= 600


@pytest.mark.anyio
async def test_initial_intent_signing_floors_remaining_ttl_after_db_delay() -> None:
    class DelaySavingRepo(FakeAssetRepository):
        async def save_asset_with_initial_version(
            self,
            asset: Asset,
            version: AssetVersion,
            idempotency: AssetUploadIdempotency,
        ) -> tuple[Asset, AssetVersion]:
            # Simulate 20s DB delay by backdating deadline before return
            version.upload_expires_at = datetime.now(UTC) + timedelta(seconds=880)
            return await super().save_asset_with_initial_version(asset, version, idempotency)

    repo = DelaySavingRepo()
    storage = RecordingStorage()
    store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )

    intent = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=1024,
        idempotency_key="key-initial-delay",
    )
    assert storage.last_ttl_seconds is not None
    # Must floor remaining seconds, never upward clamped back to 900
    assert storage.last_ttl_seconds <= 880
    assert intent.expires_at == repo.versions[intent.asset_version_id].upload_expires_at

    # If DB delay exceeds entire deadline, it fails closed with conflict
    class ExpiredSavingRepo(FakeAssetRepository):
        async def save_asset_with_initial_version(
            self,
            asset: Asset,
            version: AssetVersion,
            idempotency: AssetUploadIdempotency,
        ) -> tuple[Asset, AssetVersion]:
            version.upload_expires_at = datetime.now(UTC) - timedelta(seconds=5)
            return await super().save_asset_with_initial_version(asset, version, idempotency)

    repo_expired = ExpiredSavingRepo()
    store_expired = AssetStore(
        repository=repo_expired,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )
    with pytest.raises(AssetIdempotencyConflict, match="expired"):
        await store_expired.create_upload_intent(
            project_id=PROJECT_ID,
            user_id=MEMBER_ID,
            kind="supporting_document",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1024,
            idempotency_key="key-initial-expired",
        )


@pytest.mark.anyio
async def test_replay_upload_intent_expired_raises_safe_conflict() -> None:
    repo = FakeAssetRepository()
    storage = RecordingStorage()
    store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )

    intent = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=1024,
        idempotency_key="key-expired",
    )

    # Expire the upload deadline
    ver = repo.versions[intent.asset_version_id]
    ver.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(AssetIdempotencyConflict, match="expired"):
        await store.create_upload_intent(
            project_id=PROJECT_ID,
            user_id=MEMBER_ID,
            kind="supporting_document",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1024,
            idempotency_key="key-expired",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["verified", "rejected", "deleting", "deleted"])
async def test_replay_upload_intent_non_pending_raises_safe_conflict(state: str) -> None:
    repo = FakeAssetRepository()
    storage = RecordingStorage()
    store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )

    intent = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=1024,
        idempotency_key=f"key-state-{state}",
    )

    ver = repo.versions[intent.asset_version_id]
    ver.state = state

    with pytest.raises(AssetIdempotencyConflict, match="Cannot replay"):
        await store.create_upload_intent(
            project_id=PROJECT_ID,
            user_id=MEMBER_ID,
            kind="supporting_document",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1024,
            idempotency_key=f"key-state-{state}",
        )


@pytest.mark.anyio
async def test_sqlalchemy_repo_concurrent_replay_branch_checks_state_and_expiry(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, project_id = seed_db
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        asset_id = uuid4()
        version_id = uuid4()

        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="doc.pdf",
            current_version_id=version_id,
            created_by=user_id,
            created_at=now,
        )
        version = AssetVersion(
            id=version_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key="test-key",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1024,
            created_by=user_id,
            created_at=now,
            upload_expires_at=now - timedelta(seconds=10),  # expired
        )
        idempotency = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key="concurrent-key-1",
            request_hash="hash-1",
            asset_id=asset_id,
            version_id=version_id,
        )

        repo = SqlAlchemyAssetRepository(session)
        await repo.save_asset_with_initial_version(asset, version, idempotency)
        await session.commit()

    # save_asset_with_initial_version with same idempotency key triggers
    # IntegrityError on idempotency table
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        concurrent_asset_id = uuid4()
        concurrent_version_id = uuid4()
        concurrent_asset = Asset(
            id=concurrent_asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="doc.pdf",
            current_version_id=concurrent_version_id,
            created_by=user_id,
            created_at=now,
        )
        concurrent_version = AssetVersion(
            id=concurrent_version_id,
            asset_id=concurrent_asset_id,
            version_number=1,
            state="pending_upload",
            storage_key="test-key-2",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1024,
            created_by=user_id,
            created_at=now,
            upload_expires_at=now + timedelta(seconds=900),
        )
        concurrent_idempotency = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key="concurrent-key-1",
            request_hash="hash-1",
            asset_id=concurrent_asset_id,
            version_id=concurrent_version_id,
        )
        with pytest.raises(AssetIdempotencyConflict, match="expired"):
            await repo.save_asset_with_initial_version(
                concurrent_asset, concurrent_version, concurrent_idempotency
            )


@pytest.mark.anyio
async def test_expired_pending_completion_allowed_before_claim_vs_rejected_after_claim() -> None:
    repo = FakeAssetRepository()
    storage = RecordingStorage()
    store = AssetStore(
        repository=repo,
        storage=storage,
        document_verifier=DocumentVerifier(),
        upload_ttl_seconds=900,
    )

    pdf_bytes = make_pdf()
    import hashlib

    checksum = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"

    intent = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=len(pdf_bytes),
        idempotency_key="key-comp-1",
    )
    storage.objects[repo.versions[intent.asset_version_id].storage_key] = pdf_bytes

    # Version upload_expires_at is expired, but NOT claimed yet
    repo.versions[intent.asset_version_id].upload_expires_at = datetime.now(UTC) - timedelta(
        seconds=100
    )

    completed = await store.complete_upload(
        asset_id=intent.asset_id,
        version_id=intent.asset_version_id,
        user_id=MEMBER_ID,
        checksum=checksum,
        size_bytes=len(pdf_bytes),
        idempotency_key="complete-key-1",
    )
    assert completed.state == "verified"

    # Now create another version and mark it claimed (deleting)
    intent2 = await store.create_upload_intent(
        project_id=PROJECT_ID,
        user_id=MEMBER_ID,
        kind="supporting_document",
        file_name="doc2.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=len(pdf_bytes),
        idempotency_key="key-comp-2",
    )
    repo.versions[intent2.asset_version_id].state = "deleting"

    with pytest.raises(AssetCompletionConflict, match="being deleted"):
        await store.complete_upload(
            asset_id=intent2.asset_id,
            version_id=intent2.asset_version_id,
            user_id=MEMBER_ID,
            checksum=checksum,
            size_bytes=len(pdf_bytes),
            idempotency_key="complete-key-2",
        )


@pytest.mark.anyio
async def test_cleanup_keeps_surviving_verified_current_pointer(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, project_id = seed_db
    storage = RecordingStorage()
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        asset_id = uuid4()
        ver1_id = uuid4()
        ver2_id = uuid4()

        # Seed verified version 1
        asset_model = AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="verified",
            file_name="doc.pdf",
            current_version_id=ver1_id,
            created_by=user_id,
            created_at=now - timedelta(days=2),
        )
        ver1_model = AssetVersionModel(
            id=ver1_id,
            asset_id=asset_id,
            version_number=1,
            state="verified",
            storage_key=f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{ver1_id}.pdf",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=now - timedelta(days=2),
            upload_expires_at=now - timedelta(days=2),
        )
        # Seed replacement version 2 that is abandoned
        ver2_model = AssetVersionModel(
            id=ver2_id,
            asset_id=asset_id,
            version_number=2,
            state="pending_upload",
            storage_key=f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{ver2_id}.pdf",
            file_name="doc-v2.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=2000,
            created_by=user_id,
            created_at=now - timedelta(days=2),
            upload_expires_at=now - timedelta(days=1),
        )
        session.add_all([asset_model, ver1_model, ver2_model])
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(
            repository=repo,
            storage=storage,
            document_verifier=DocumentVerifier(),
        )
        count = await store.cleanup_abandoned_uploads(
            batch_size=10,
            retention_seconds=86400,
        )
        assert count == 1

    # Verify that asset state is still verified and current_version_id is still ver1
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        asset = await repo.get_asset(asset_id)
        assert asset is not None
        assert asset.state == "verified"
        assert asset.current_version_id == ver1_id

        ver2 = await repo.get_version(ver2_id)
        assert ver2 is not None
        assert ver2.state == "deleted"


@pytest.mark.anyio
async def test_cleanup_initial_abandoned_version_marks_asset_deleted(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, project_id = seed_db
    storage = RecordingStorage()
    now = datetime.now(UTC)

    asset_id = uuid4()
    ver_id = uuid4()

    async with db_session_factory() as session:
        asset_model = AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="initial.pdf",
            current_version_id=ver_id,
            created_by=user_id,
            created_at=now - timedelta(days=2),
        )
        ver_model = AssetVersionModel(
            id=ver_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key=f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{ver_id}.pdf",
            file_name="initial.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=now - timedelta(days=2),
            upload_expires_at=now - timedelta(days=1),
        )
        session.add_all([asset_model, ver_model])
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(
            repository=repo,
            storage=storage,
            document_verifier=DocumentVerifier(),
        )
        count = await store.cleanup_abandoned_uploads(
            batch_size=10,
            retention_seconds=86400,
        )
        assert count == 1

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        asset = await repo.get_asset(asset_id)
        assert asset is not None
        assert asset.state == "deleted"


@pytest.mark.anyio
async def test_cleanup_handles_storage_failure_and_retries_with_lease(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id, team_id, project_id = seed_db
    storage = RecordingStorage()
    storage.fail_delete = True  # Storage delete will fail
    now = datetime.now(UTC)

    asset_id = uuid4()
    ver_id = uuid4()

    async with db_session_factory() as session:
        asset_model = AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="fail.pdf",
            current_version_id=ver_id,
            created_by=user_id,
            created_at=now - timedelta(days=2),
        )
        ver_model = AssetVersionModel(
            id=ver_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key="test-storage-key",
            file_name="fail.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=now - timedelta(days=2),
            upload_expires_at=now - timedelta(days=1),
        )
        session.add_all([asset_model, ver_model])
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(
            repository=repo,
            storage=storage,
            document_verifier=DocumentVerifier(),
        )
        cleaned = await store.cleanup_abandoned_uploads(
            batch_size=10,
            retention_seconds=86400,
            lease_seconds=300,
        )
        assert cleaned == 0  # Failed storage delete does not finalize
        assert "Asset cleanup storage deletion failed; retry scheduled" in caplog.text

    # Verify version state is deleting with cleanup_next_attempt_at in future
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        ver = await repo.get_version(ver_id)
        assert ver is not None
        assert ver.state == "deleting"
        assert ver.cleanup_next_attempt_at is not None
        assert ver.cleanup_next_attempt_at > datetime.now(UTC)

    # Now recover storage, but keep next_attempt_at in the future -> candidate query ignores it
    storage.fail_delete = False
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(
            repository=repo,
            storage=storage,
            document_verifier=DocumentVerifier(),
        )
        cleaned2 = await store.cleanup_abandoned_uploads(
            batch_size=10,
            retention_seconds=86400,
        )
        assert cleaned2 == 0

    # Advance time past cleanup_next_attempt_at -> now it is picked up and cleaned!
    async with db_session_factory() as session:
        ver_stmt = select(AssetVersionModel).where(AssetVersionModel.id == ver_id)
        ver_row = await session.scalar(ver_stmt)
        assert ver_row is not None
        ver_row.cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(
            repository=repo,
            storage=storage,
            document_verifier=DocumentVerifier(),
        )
        cleaned3 = await store.cleanup_abandoned_uploads(
            batch_size=10,
            retention_seconds=86400,
        )
        assert cleaned3 == 1


@pytest.mark.anyio
async def test_cleanup_tombstone_resweep_and_fair_batch_ordering(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, project_id = seed_db
    now = datetime.now(UTC)

    fresh_ver_id = uuid4()
    tombstone_ver_id = uuid4()
    asset_id = uuid4()

    async with db_session_factory() as session:
        asset_model = AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="deleted",
            file_name="test.pdf",
            created_by=user_id,
            created_at=now - timedelta(days=5),
        )
        # Deleted tombstone whose delayed attempt is now due
        tombstone_ver = AssetVersionModel(
            id=tombstone_ver_id,
            asset_id=asset_id,
            version_number=1,
            state="deleted",
            storage_key="key-tombstone",
            file_name="tombstone.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=500,
            created_by=user_id,
            created_at=now - timedelta(days=5),
            upload_expires_at=now - timedelta(days=4),
            cleanup_next_attempt_at=now - timedelta(seconds=10),
        )
        # Fresh pending_upload candidate whose cleanup_next_attempt_at is NULL
        fresh_ver = AssetVersionModel(
            id=fresh_ver_id,
            asset_id=asset_id,
            version_number=2,
            state="pending_upload",
            storage_key="key-fresh",
            file_name="fresh.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=500,
            created_by=user_id,
            created_at=now - timedelta(days=2),
            upload_expires_at=now - timedelta(days=1),
            cleanup_next_attempt_at=None,
        )
        session.add_all([asset_model, tombstone_ver, fresh_ver])
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        # Check ordering: NULLS FIRST means fresh candidate comes before tombstone
        candidates = await repo.find_cleanup_candidate_versions(
            cutoff_created_at=now - timedelta(seconds=86400),
            cutoff_expires_at=now,
            now=now,
            limit=10,
        )
        assert len(candidates) == 2
        # First candidate must be fresh_ver (NULL cleanup_next_attempt_at)
        assert candidates[0][1] == fresh_ver_id
        assert candidates[1][1] == tombstone_ver_id


@pytest.mark.anyio
async def test_cleanup_requires_both_deadline_and_retention_cutoff(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    user_id, team_id, project_id = seed_db
    now = datetime.now(UTC)

    asset_id = uuid4()
    # Case A: created long ago, but upload_expires_at is still in future (not eligible)
    ver_a_id = uuid4()
    # Case B: upload_expires_at expired, but created recently (< retention_seconds) (not eligible)
    ver_b_id = uuid4()
    # Case C: BOTH created long ago AND upload_expires_at expired (ELIGIBLE)
    ver_c_id = uuid4()

    async with db_session_factory() as session:
        asset_model = AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="test.pdf",
            created_by=user_id,
            created_at=now - timedelta(days=3),
        )
        ver_a = AssetVersionModel(
            id=ver_a_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key="key-a",
            file_name="a.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=100,
            created_by=user_id,
            created_at=now - timedelta(days=3),
            upload_expires_at=now + timedelta(hours=1),  # in future!
        )
        ver_b = AssetVersionModel(
            id=ver_b_id,
            asset_id=asset_id,
            version_number=2,
            state="pending_upload",
            storage_key="key-b",
            file_name="b.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=100,
            created_by=user_id,
            created_at=now - timedelta(hours=2),  # created recently (< 24h)
            upload_expires_at=now - timedelta(minutes=30),  # expired
        )
        ver_c = AssetVersionModel(
            id=ver_c_id,
            asset_id=asset_id,
            version_number=3,
            state="pending_upload",
            storage_key="key-c",
            file_name="c.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=100,
            created_by=user_id,
            created_at=now - timedelta(days=2),  # > 24h
            upload_expires_at=now - timedelta(hours=20),  # expired
        )
        session.add_all([asset_model, ver_a, ver_b, ver_c])
        await session.commit()

    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        candidates = await repo.find_cleanup_candidate_versions(
            cutoff_created_at=now - timedelta(seconds=86400),
            cutoff_expires_at=now,
            now=now,
            limit=10,
        )
        candidate_ids = [cid for _, cid in candidates]
        assert ver_a_id not in candidate_ids
        assert ver_b_id not in candidate_ids
        assert ver_c_id in candidate_ids


@pytest.mark.anyio
async def test_scheduler_loop_resilience_and_shutdown_cancellation(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        asset_cleanup_enabled=True,
        asset_cleanup_interval_seconds=0.01,
        asset_cleanup_batch_size=5,
    )

    app = create_app(settings)

    call_count = 0
    success_event = asyncio.Event()

    class FakeCleanupStore:
        async def cleanup_abandoned_uploads(self, **kwargs: Any) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated transient cleanup failure")
            success_event.set()
            return 1

    app.state.asset_store_factory = lambda session: FakeCleanupStore()

    # Enter lifespan to start the cleanup task
    async with app.router.lifespan_context(app):
        # Must recover after transient error and tick successfully
        await asyncio.wait_for(success_event.wait(), timeout=1.0)
        assert call_count >= 2

    calls_at_exit = call_count
    await asyncio.sleep(0.03)
    # Task was cancelled on shutdown; no further calls
    assert call_count == calls_at_exit

    # Exceptional lifespan exit must also clean resources (task cancelled, engine disposed)
    with pytest.raises(RuntimeError, match="Lifespan crash"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("Lifespan crash")
