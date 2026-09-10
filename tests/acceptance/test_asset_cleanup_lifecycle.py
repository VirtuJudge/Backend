import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.asset_store import AssetStore
from app.domain.asset import AssetCompletionConflict
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from tests.support import (
    MEMBER_ID,
    PROJECT_ID,
    FakeAssetRepository,
    RecordingStorage,
    make_pdf,
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
@pytest.mark.parametrize("old_state", ["pending_upload", "rejected"])
async def test_cleanup_preserves_fresh_pending_replacement(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    old_state: str,
) -> None:
    user_id, _, project_id = seed_db
    storage = RecordingStorage()
    data = make_pdf()
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        store = AssetStore(repo, storage, DocumentVerifier())
        original = await store.create_upload_intent(
            project_id,
            user_id,
            "supporting_document",
            "old.pdf",
            "application/pdf",
            len(data),
            "old",
        )
        old_time = datetime.now(UTC) - timedelta(days=2)
        await session.execute(
            update(AssetModel)
            .where(AssetModel.id == original.asset_id)
            .values(state=old_state, created_at=old_time)
        )
        await session.execute(
            update(AssetVersionModel)
            .where(AssetVersionModel.id == original.asset_version_id)
            .values(state=old_state, created_at=old_time, upload_expires_at=old_time)
        )
        await session.commit()
        replacement = await store.create_version_upload_intent(
            original.asset_id, user_id, "new.pdf", "application/pdf", len(data), "new"
        )
        version = await repo.get_version(replacement.asset_version_id)
        assert version is not None
        storage.objects[version.storage_key] = data
        assert await store.cleanup_abandoned_uploads() == 1
        asset = await store.get_asset(original.asset_id, user_id)
        assert asset.state == "pending_upload"
        assert asset.current_version_id == version.id
        completed = await store.complete_upload(
            asset.id,
            version.id,
            user_id,
            "sha256:" + hashlib.sha256(data).hexdigest(),
            len(data),
            "done",
        )
        assert completed.state == "verified"
        assert version.storage_key not in storage.deleted_keys
