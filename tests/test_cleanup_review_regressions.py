import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.assetStore import AssetStore
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.persistence.configurations.assetConfiguration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.repositories.sqlalchemyAssetRepository import SqlAlchemyAssetRepository
from tests.test_asset_acceptance import make_pdf
from tests.test_asset_cleanup import RecordingStorage, db_session_factory, seed_db

__all__ = ["db_session_factory", "seed_db"]


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
