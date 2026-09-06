import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.asset import Asset, AssetIdempotencyConflict, AssetVersion
from app.domain.idempotency import AssetUploadIdempotency
from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemyAssetRepository import SqlAlchemyAssetRepository

NOW = datetime.now(UTC)


@pytest.fixture
async def db_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    test_pg_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if test_pg_url:
        engine = create_async_engine(test_pg_url, echo=False)
    else:
        db_path = tmp_path / "asset_repo_test.db"
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
async def async_db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


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
async def test_simultaneous_concurrent_create_race_matching_payload(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()

    async with db_session_factory() as setup_session:
        user = UserModel(
            id=user_id,
            email=f"race-{uuid4().hex[:8]}@example.com",
            issuer="https://auth.example",
            subject=f"sub-race-{uuid4().hex[:8]}",
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
            name="Race Project",
            description=None,
            created_at=NOW,
        )
        setup_session.add_all([user, team, member, project])
        await setup_session.commit()

    async def execute_request(asset_id: UUID, version_id: UUID) -> tuple[Asset, AssetVersion]:
        async with db_session_factory() as session:
            repo = SqlAlchemyAssetRepository(session)
            asset = Asset(
                id=asset_id,
                project_id=project_id,
                kind="supporting_document",
                state="pending_upload",
                file_name="race.pdf",
                current_version_id=version_id,
                created_by=user_id,
                created_at=NOW,
            )
            version = AssetVersion(
                id=version_id,
                asset_id=asset_id,
                version_number=1,
                state="pending_upload",
                storage_key=f"key-{uuid4()}",
                file_name="race.pdf",
                declared_media_type="application/pdf",
                declared_size_bytes=1000,
                created_by=user_id,
                created_at=NOW,
            )
            idem = AssetUploadIdempotency(
                user_id=user_id,
                project_id=project_id,
                operation="upload_intent",
                key="simultaneous-key",
                request_hash="identical-hash",
                asset_id=asset_id,
                version_id=version_id,
            )
            res_asset, res_version = await repo.save_asset_with_initial_version(
                asset, version, idem
            )
            await session.commit()
            return res_asset, res_version

    task1 = asyncio.create_task(execute_request(uuid4(), uuid4()))
    task2 = asyncio.create_task(execute_request(uuid4(), uuid4()))
    (asset1, ver1), (asset2, ver2) = await asyncio.gather(task1, task2)

    assert asset1.id == asset2.id
    assert ver1.id == ver2.id


@pytest.mark.anyio
async def test_simultaneous_concurrent_create_race_conflicting_payload(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()

    async with db_session_factory() as setup_session:
        user = UserModel(
            id=user_id,
            email=f"race-{uuid4().hex[:8]}@example.com",
            issuer="https://auth.example",
            subject=f"sub-race-{uuid4().hex[:8]}",
            created_at=NOW,
        )
        team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
        project = ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Race Project Conflicting",
            description=None,
            created_at=NOW,
        )
        setup_session.add_all([user, team, project])
        await setup_session.commit()

    async def execute_request(
        asset_id: UUID, version_id: UUID, req_hash: str
    ) -> tuple[Asset, AssetVersion]:
        async with db_session_factory() as session:
            repo = SqlAlchemyAssetRepository(session)
            asset = Asset(
                id=asset_id,
                project_id=project_id,
                kind="supporting_document",
                state="pending_upload",
                file_name="race.pdf",
                current_version_id=version_id,
                created_by=user_id,
                created_at=NOW,
            )
            version = AssetVersion(
                id=version_id,
                asset_id=asset_id,
                version_number=1,
                state="pending_upload",
                storage_key=f"key-{uuid4()}",
                file_name="race.pdf",
                declared_media_type="application/pdf",
                declared_size_bytes=1000,
                created_by=user_id,
                created_at=NOW,
            )
            idem = AssetUploadIdempotency(
                user_id=user_id,
                project_id=project_id,
                operation="upload_intent",
                key="conflict-race-key",
                request_hash=req_hash,
                asset_id=asset_id,
                version_id=version_id,
            )
            res_asset, res_version = await repo.save_asset_with_initial_version(
                asset, version, idem
            )
            await session.commit()
            return res_asset, res_version

    task1 = asyncio.create_task(execute_request(uuid4(), uuid4(), "hash-A"))
    task2 = asyncio.create_task(execute_request(uuid4(), uuid4(), "hash-B"))
    results = await asyncio.gather(task1, task2, return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, AssetIdempotencyConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1


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
