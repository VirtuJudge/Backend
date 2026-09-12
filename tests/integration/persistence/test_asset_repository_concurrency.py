import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.asset import Asset, AssetIdempotencyConflict, AssetVersion
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
async def test_version_allocation_idempotency_and_consistent_metadata(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    v1_id = uuid4()

    async with db_session_factory() as setup_session:
        user = UserModel(
            id=user_id,
            email=f"ver-{uuid4().hex[:8]}@example.com",
            issuer="https://auth.example",
            subject=f"sub-ver-{uuid4().hex[:8]}",
            created_at=NOW,
        )
        team = TeamModel(id=team_id, name=f"Team-{uuid4().hex[:8]}", created_at=NOW)
        project = ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Version Project",
            description=None,
            created_at=NOW,
        )
        setup_session.add_all([user, team, project])
        await setup_session.commit()

        repo = SqlAlchemyAssetRepository(setup_session)
        init_asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind="supporting_document",
            state="verified",
            file_name="doc.pdf",
            current_version_id=v1_id,
            created_by=user_id,
            created_at=NOW,
        )
        init_ver = AssetVersion(
            id=v1_id,
            asset_id=asset_id,
            version_number=1,
            state="verified",
            storage_key=f"key-v1-{uuid4()}",
            file_name="doc.pdf",
            declared_media_type="application/pdf",
            declared_size_bytes=1000,
            created_by=user_id,
            created_at=NOW,
        )
        idem = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key="init-key",
            request_hash="init-hash",
            asset_id=asset_id,
            version_id=v1_id,
        )
        await repo.save_asset_with_initial_version(init_asset, init_ver, idem)
        await setup_session.commit()

    async def create_replacement(v_id: UUID, key: str, req_hash: str) -> AssetVersion:
        async with db_session_factory() as session:
            repo = SqlAlchemyAssetRepository(session)
            ver = AssetVersion(
                id=v_id,
                asset_id=asset_id,
                version_number=0,
                state="pending_upload",
                storage_key=f"key-{v_id}",
                file_name=f"rep_{key}.pdf",
                declared_media_type="application/pdf",
                declared_size_bytes=1000,
                created_by=user_id,
                created_at=NOW,
            )
            idem = AssetUploadIdempotency(
                user_id=user_id,
                project_id=project_id,
                operation=f"version_upload:{asset_id.hex}",
                key=key,
                request_hash=req_hash,
                asset_id=asset_id,
                version_id=v_id,
            )
            _, saved_ver = await repo.save_replacement_version(asset_id, ver, idem)
            return saved_ver

    # SQLite has no row locks; concurrency is exercised against PostgreSQL.
    res_v2 = await create_replacement(uuid4(), "k2", "h2")
    res_v3 = await create_replacement(uuid4(), "k3", "h3")
    assert {res_v2.version_number, res_v3.version_number} == {2, 3}
    replay = await create_replacement(uuid4(), "k2", "h2")
    assert replay.id == res_v2.id
    with pytest.raises(AssetIdempotencyConflict):
        await create_replacement(uuid4(), "k2", "changed")

    # 3. Complete v3 and verify consistent asset and version metadata
    async with db_session_factory() as session:
        repo = SqlAlchemyAssetRepository(session)
        asset_obj, target_ver, current_ver = await repo.get_asset_and_version_for_completion(
            asset_id, res_v3.id
        )
        assert asset_obj is not None and target_ver is not None
        assert current_ver is not None and current_ver.id == v1_id

        target_ver.state = "verified"
        target_ver.size_bytes = 1000
        target_ver.checksum = "sha256:abcd"
        target_ver.media_type = "application/pdf"
        target_ver.completed_at = NOW

        asset_obj.state = "verified"
        asset_obj.file_name = target_ver.file_name
        asset_obj.current_version_id = target_ver.id
        asset_obj.size_bytes = 1000
        asset_obj.checksum = "sha256:abcd"
        asset_obj.media_type = "application/pdf"

        await repo.save_version_completion(target_ver, asset_obj)

        updated_asset = await repo.get_asset(asset_id)
        assert updated_asset is not None
        assert updated_asset.current_version_id == res_v3.id
        assert updated_asset.file_name == res_v3.file_name
        assert updated_asset.state == "verified"
