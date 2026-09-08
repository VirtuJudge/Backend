from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.asset_store import AssetStore
from app.domain.asset import (
    Asset,
    AssetIdempotencyConflict,
    AssetVersion,
)
from app.domain.idempotency import AssetUploadIdempotency
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from tests.support import (
    MEMBER_ID,
    PROJECT_ID,
    FakeAssetRepository,
    RecordingStorage,
)


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
