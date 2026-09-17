from uuid import uuid4

import pytest

from app.domain.user import User
from tests.support import (
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    create_test_client,
)


@pytest.mark.anyio
async def test_asset_delete_as_team_member(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        # Create an asset
        init_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "document.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 1024,
            },
            headers={"Idempotency-Key": "test-del-1"},
        )
        assert init_res.status_code == 201
        asset_id = init_res.json()["asset_id"]

        # Delete the asset via global asset route
        del_res = await client.delete(f"/api/v1/assets/{asset_id}")
        assert del_res.status_code == 204
        assert not del_res.content

        # Asset should not be found anymore
        get_res = await client.get(f"/api/v1/assets/{asset_id}")
        assert get_res.status_code == 404


@pytest.mark.anyio
async def test_asset_delete_via_project_scoped_route(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
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
        asset_id = init_res.json()["asset_id"]

        # Delete the asset via project scoped route
        del_res = await client.delete(f"/api/v1/projects/{PROJECT_ID}/assets/{asset_id}")
        assert del_res.status_code == 204
        assert not del_res.content

        # Asset should not be found anymore
        get_res = await client.get(f"/api/v1/assets/{asset_id}")
        assert get_res.status_code == 404


@pytest.mark.anyio
async def test_asset_delete_forbidden_for_non_member(
    member_user: User, outsider_user: User
) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        init_res = await client.post(
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

    async with create_test_client(outsider_user, repo, storage) as client:
        del_res = await client.delete(f"/api/v1/assets/{asset_id}")
        assert del_res.status_code == 404
        assert del_res.json()["code"] == "not_found"


@pytest.mark.anyio
async def test_asset_delete_not_found(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        del_res = await client.delete(f"/api/v1/assets/{uuid4()}")
        assert del_res.status_code == 404
