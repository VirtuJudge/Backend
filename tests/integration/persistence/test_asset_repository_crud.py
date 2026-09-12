from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.asset import Asset, AssetVersion
from app.domain.idempotency import AssetUploadIdempotency
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository

NOW = datetime.now(UTC)


@pytest.mark.anyio
async def test_sqlalchemy_asset_repository_save_and_retrieve(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repo = SqlAlchemyAssetRepository(session)

    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()

    user = UserModel(
        id=user_id,
        email=f"user-{uuid4().hex[:8]}@example.com",
        issuer="https://auth.example",
        subject=f"sub-{uuid4().hex[:8]}",
        created_at=NOW,
    )
    team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
    member = TeamMemberModel(
        id=uuid4(),
        team_id=team_id,
        user_id=user_id,
        role="owner",
        joined_at=NOW,
    )
    project = ProjectModel(
        id=project_id,
        team_id=team_id,
        name="Project 1",
        description="Desc",
        created_at=NOW,
    )
    session.add_all([user, team, member, project])
    await session.commit()

    asset = Asset(
        id=asset_id,
        project_id=project_id,
        kind="supporting_document",
        state="pending_upload",
        file_name="doc.pdf",
        current_version_id=version_id,
        created_by=user_id,
        created_at=NOW,
    )
    version = AssetVersion(
        id=version_id,
        asset_id=asset_id,
        version_number=1,
        state="pending_upload",
        storage_key=f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{version_id}.pdf",
        file_name="doc.pdf",
        declared_media_type="application/pdf",
        declared_size_bytes=2048,
        created_by=user_id,
        created_at=NOW,
    )
    idempotency = AssetUploadIdempotency(
        user_id=user_id,
        project_id=project_id,
        operation="upload_intent",
        key="idem-key-1",
        request_hash="hash123",
        asset_id=asset_id,
        version_id=version_id,
    )

    saved_asset, saved_version = await repo.save_asset_with_initial_version(
        asset, version, idempotency
    )
    await session.commit()

    assert saved_asset.id == asset_id
    assert saved_version.id == version_id

    fetched_asset = await repo.get_asset(asset_id)
    assert fetched_asset is not None
    assert fetched_asset.file_name == "doc.pdf"
    assert fetched_asset.state == "pending_upload"

    fetched_version = await repo.get_version(version_id)
    assert fetched_version is not None
    assert fetched_version.declared_size_bytes == 2048

    idem = await repo.get_upload_idempotency(user_id, project_id, "upload_intent", "idem-key-1")
    assert idem is not None
    assert idem.request_hash == "hash123"

    version.state = "verified"
    version.size_bytes = 2048
    version.checksum = "sha256:abcd"
    asset.state = "verified"
    asset.size_bytes = 2048
    asset.checksum = "sha256:abcd"
    await repo.save_version_completion(version, asset)

    updated_asset = await repo.get_asset(asset_id)
    assert updated_asset is not None
    assert updated_asset.state == "verified"
    assert updated_asset.checksum == "sha256:abcd"


@pytest.mark.anyio
async def test_sqlalchemy_asset_repository_list_and_filters(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repo = SqlAlchemyAssetRepository(session)

    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()

    user = UserModel(
        id=user_id,
        email=f"list-{uuid4().hex[:8]}@example.com",
        issuer="https://auth.example",
        subject=f"sub-list-{uuid4().hex[:8]}",
        created_at=NOW,
    )
    team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
    project = ProjectModel(
        id=project_id,
        team_id=team_id,
        name="List Project",
        description=None,
        created_at=NOW,
    )
    session.add_all([user, team, project])
    await session.commit()

    for i in range(3):
        aid = uuid4()
        vid = uuid4()
        state = "verified" if i == 0 else "pending_upload"
        asset = Asset(
            id=aid,
            project_id=project_id,
            kind="supporting_document",
            state=state,
            file_name=f"doc_{i}.pdf",
            current_version_id=vid,
            created_by=user_id,
            created_at=NOW,
        )
        version = AssetVersion(
            id=vid,
            asset_id=aid,
            version_number=1,
            state=state,
            storage_key=f"key-{uuid4()}",
            file_name=f"doc_{i}.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=NOW,
        )
        idem = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key=f"list-key-{i}",
            request_hash=f"hash-{i}",
            asset_id=aid,
            version_id=vid,
        )
        await repo.save_asset_with_initial_version(asset, version, idem)
    await session.commit()

    all_assets, _ = await repo.list_assets(project_id, cursor=None, limit=10, kind=None, state=None)
    assert len(all_assets) == 3

    verified_assets, _ = await repo.list_assets(
        project_id, cursor=None, limit=10, kind=None, state="verified"
    )
    assert len(verified_assets) == 1

    pending_assets, _ = await repo.list_assets(
        project_id, cursor=None, limit=10, kind=None, state="pending_upload"
    )
    assert len(pending_assets) == 2

    page1, cursor1 = await repo.list_assets(project_id, cursor=None, limit=2, kind=None, state=None)
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2 = await repo.list_assets(
        project_id, cursor=cursor1, limit=2, kind=None, state=None
    )
    assert len(page2) == 1
    assert cursor2 is None


@pytest.mark.anyio
async def test_sqlalchemy_asset_repository_media_retention_and_duration(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repo = SqlAlchemyAssetRepository(session)

    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()

    user = UserModel(
        id=user_id,
        email=f"user-{uuid4().hex[:8]}@example.com",
        issuer="https://auth.example",
        subject=f"sub-{uuid4().hex[:8]}",
        created_at=NOW,
    )
    team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
    project = ProjectModel(
        id=project_id,
        team_id=team_id,
        name="Media Project",
        description=None,
        created_at=NOW,
    )
    session.add_all([user, team, project])
    await session.commit()

    retention_expires = NOW + timedelta(days=30)
    asset = Asset(
        id=asset_id,
        project_id=project_id,
        kind="presentation_video",
        state="pending_upload",
        file_name="pitch.mp4",
        current_version_id=version_id,
        created_by=user_id,
        created_at=NOW,
        retention_expires_at=retention_expires,
    )
    version = AssetVersion(
        id=version_id,
        asset_id=asset_id,
        version_number=1,
        state="pending_upload",
        storage_key=f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{version_id}.mp4",
        file_name="pitch.mp4",
        declared_media_type="video/mp4",
        declared_size_bytes=100000,
        created_by=user_id,
        created_at=NOW,
    )
    idem = AssetUploadIdempotency(
        user_id=user_id,
        project_id=project_id,
        operation="upload_intent",
        key="media-key-1",
        request_hash="hash-media",
        asset_id=asset_id,
        version_id=version_id,
    )
    await repo.save_asset_with_initial_version(asset, version, idem)
    await session.commit()

    # Verify initial retention_expires_at is persisted
    fetched = await repo.get_asset(asset_id)
    assert fetched is not None
    assert fetched.retention_expires_at is not None
    assert fetched.kind == "presentation_video"

    # Complete upload with duration
    locked_asset, locked_ver, _ = await repo.get_asset_and_version_for_completion(
        asset_id, version_id
    )
    assert locked_asset is not None and locked_ver is not None

    locked_ver.state = "verified"
    locked_ver.size_bytes = 100000
    locked_ver.checksum = "sha256:112233"
    locked_ver.media_type = "video/mp4"
    locked_ver.duration_ms = 45000
    locked_ver.completed_at = NOW

    locked_asset.state = "verified"
    locked_asset.file_name = locked_ver.file_name
    locked_asset.size_bytes = 100000
    locked_asset.checksum = "sha256:112233"
    locked_asset.media_type = "video/mp4"
    locked_asset.current_version_id = locked_ver.id
    locked_asset.duration_ms = 45000
    locked_asset.retention_expires_at = retention_expires

    await repo.save_version_completion(locked_ver, locked_asset)

    completed_asset = await repo.get_asset(asset_id)
    assert completed_asset is not None
    assert completed_asset.state == "verified"
    assert completed_asset.duration_ms == 45000
    assert completed_asset.retention_expires_at is not None

    completed_ver = await repo.get_version(version_id)
    assert completed_ver is not None
    assert completed_ver.state == "verified"
    assert completed_ver.duration_ms == 45000


@pytest.mark.anyio
async def test_get_asset_and_version_populate_existing_refreshes_prefilled_identity_map(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()

    async with db_session_factory() as session:
        user = UserModel(
            id=user_id,
            email=f"user-{uuid4().hex[:8]}@example.com",
            issuer="https://auth.example",
            subject=f"sub-{uuid4().hex[:8]}",
            created_at=NOW,
        )
        team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
        project = ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Refresh Project",
            description=None,
            created_at=NOW,
        )
        session.add_all([user, team, project])
        await session.commit()

        repo = SqlAlchemyAssetRepository(session)
        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="pending_upload",
            file_name="refresh.pdf",
            current_version_id=version_id,
            created_by=user_id,
            created_at=NOW,
        )
        version = AssetVersion(
            id=version_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key=f"key-{uuid4()}.pdf",
            file_name="refresh.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=NOW,
        )
        idem = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key="refresh-key-1",
            request_hash="hash-refresh",
            asset_id=asset_id,
            version_id=version_id,
        )
        await repo.save_asset_with_initial_version(asset, version, idem)
        await session.commit()

    # Now open session 1: prefill identity map with an authorize-style read
    async with db_session_factory() as session1:
        repo1 = SqlAlchemyAssetRepository(session1)
        stale_asset = await repo1.get_asset(asset_id)
        assert stale_asset is not None
        assert stale_asset.state == "pending_upload"

        # Concurrently in session 2: update asset state in database
        async with db_session_factory() as session2:
            repo2 = SqlAlchemyAssetRepository(session2)
            asset2, ver2, _ = await repo2.get_asset_and_version_for_completion(asset_id, version_id)
            assert asset2 is not None and ver2 is not None
            ver2.state = "verified"
            ver2.size_bytes = 1000
            ver2.checksum = "sha256:ffff"
            ver2.media_type = "application/pdf"
            asset2.state = "verified"
            await repo2.save_version_completion(ver2, asset2)

        # The original session must refresh its cached rows when it acquires the locks.
        locked_asset, locked_ver, _ = await repo1.get_asset_and_version_for_completion(
            asset_id, version_id
        )
        assert locked_asset is not None
        assert locked_asset.state == "verified"
