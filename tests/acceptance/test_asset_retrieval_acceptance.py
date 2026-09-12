import hashlib
from uuid import UUID

import pytest

from app.domain.user import User
from tests.support import (
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    create_test_client,
    make_pdf,
)


@pytest.mark.anyio
async def test_download_intent_only_allowed_for_verified_assets(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        pdf_bytes = make_pdf()
        size = len(pdf_bytes)
        checksum = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"

        init_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "download.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size,
            },
            headers={"Idempotency-Key": "dl-1"},
        )
        asset_id = init_res.json()["asset_id"]
        version_id = init_res.json()["asset_version_id"]

        pre_res = await client.post(f"/api/v1/assets/{asset_id}/download-intents")
        assert pre_res.status_code == 409
        assert pre_res.headers["content-type"] == "application/problem+json"
        assert pre_res.json()["code"] == "asset_not_verified"

        version = await repo.get_version(UUID(version_id))
        assert version is not None
        storage.objects[version.storage_key] = pdf_bytes

        await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-dl"},
        )

        dl_res = await client.post(f"/api/v1/assets/{asset_id}/download-intents")
        assert dl_res.status_code == 200
        data = dl_res.json()
        assert data["asset_id"] == asset_id
        assert data["download_url"].startswith("https://storage.example/")
        assert data["media_type"] == "application/pdf"
        assert data["size_bytes"] == size
        assert data["file_name"] == "download.pdf"


@pytest.mark.anyio
async def test_list_and_get_assets_with_filters_and_pagination(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        for i in range(3):
            await client.post(
                f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
                json={
                    "kind": "supporting_document",
                    "file_name": f"doc_{i}.pdf",
                    "declared_media_type": "application/pdf",
                    "declared_size_bytes": 1000 + i,
                },
                headers={"Idempotency-Key": f"list-key-{i}"},
            )

        list_res = await client.get(f"/api/v1/projects/{PROJECT_ID}/assets")
        assert list_res.status_code == 200
        data = list_res.json()
        assert len(data["items"]) == 3

        page1_res = await client.get(f"/api/v1/projects/{PROJECT_ID}/assets?limit=2")
        page1 = page1_res.json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None

        page2_res = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/assets?cursor={page1['next_cursor']}&limit=2"
        )
        page2 = page2_res.json()
        assert len(page2["items"]) == 1
        assert page2["next_cursor"] is None

        pending_res = await client.get(f"/api/v1/projects/{PROJECT_ID}/assets?state=pending_upload")
        assert len(pending_res.json()["items"]) == 3
        verified_res = await client.get(f"/api/v1/projects/{PROJECT_ID}/assets?state=verified")
        assert len(verified_res.json()["items"]) == 0
