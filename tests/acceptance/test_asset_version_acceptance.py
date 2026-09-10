import hashlib
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain.asset import Asset, AssetVersion
from app.domain.user import User
from tests.support import (
    ERASED_PROJECT_ID,
    MEMBER_ID,
    NOW,
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    create_test_client,
    make_pdf,
)


@pytest.mark.anyio
async def test_asset_version_workflow(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        # 1. Create initial version 1 (PDF)
        pdf_bytes = make_pdf()
        size_1 = len(pdf_bytes)
        cksum_1 = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"

        res1 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "slides_v1.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size_1,
            },
            headers={"Idempotency-Key": "v1-intent"},
        )
        assert res1.status_code == 201
        asset_id = res1.json()["asset_id"]
        v1_id = res1.json()["asset_version_id"]

        v1_obj = await repo.get_version(UUID(v1_id))
        assert v1_obj is not None
        storage.objects[v1_obj.storage_key] = pdf_bytes

        # Complete v1 -> verified
        comp_v1 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{v1_id}/complete",
            json={"checksum": cksum_1, "size_bytes": size_1},
            headers={"Idempotency-Key": "v1-comp"},
        )
        assert comp_v1.status_code == 202
        assert comp_v1.json()["version_id"] == v1_id
        assert comp_v1.json()["state"] == "verified"

        # Download v1 via asset endpoint
        dl_v1 = await client.post(f"/api/v1/assets/{asset_id}/download-intents")
        assert dl_v1.status_code == 200
        assert dl_v1.json()["asset_version_id"] == v1_id

        # 2. Create replacement version 2 intent
        res2 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/upload-intents",
            json={
                "file_name": "slides_v2.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size_1,
            },
            headers={"Idempotency-Key": "v2-intent"},
        )
        assert res2.status_code == 201
        v2_id = res2.json()["asset_version_id"]
        assert v2_id != v1_id

        # While v2 is pending, old v1 remains current and downloadable
        asset_mid = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_mid.json()["version_id"] == v1_id
        assert asset_mid.json()["state"] == "verified"
        dl_survive = await client.post(f"/api/v1/assets/{asset_id}/download-intents")
        assert dl_survive.json()["asset_version_id"] == v1_id

        # 3. Reject replacement v2 (mismatched size)
        v2_obj = await repo.get_version(UUID(v2_id))
        assert v2_obj is not None
        storage.objects[v2_obj.storage_key] = pdf_bytes
        rej_v2 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{v2_id}/complete",
            json={"checksum": cksum_1, "size_bytes": size_1 + 10},
            headers={"Idempotency-Key": "v2-comp-fail"},
        )
        assert rej_v2.status_code == 422
        # After rejection of v2, old v1 still survives as current
        asset_after_rej = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_after_rej.json()["version_id"] == v1_id
        assert asset_after_rej.json()["state"] == "verified"
        dl_after_rej = await client.post(f"/api/v1/assets/{asset_id}/download-intents")
        assert dl_after_rej.json()["asset_version_id"] == v1_id

        # 4. Create replacement version 3 intent and complete it successfully
        res3 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/upload-intents",
            json={
                "file_name": "slides_v3.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size_1,
            },
            headers={"Idempotency-Key": "v3-intent"},
        )
        assert res3.status_code == 201
        v3_id = res3.json()["asset_version_id"]

        v3_obj = await repo.get_version(UUID(v3_id))
        assert v3_obj is not None
        storage.objects[v3_obj.storage_key] = pdf_bytes

        comp_v3_1 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{v3_id}/complete",
            json={"checksum": cksum_1, "size_bytes": size_1},
            headers={"Idempotency-Key": "v3-comp"},
        )
        assert comp_v3_1.status_code == 202
        assert comp_v3_1.json()["version_id"] == v3_id

        # Exact replay response on repeated complete
        comp_v3_2 = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{v3_id}/complete",
            json={"checksum": cksum_1, "size_bytes": size_1},
            headers={"Idempotency-Key": "v3-comp-replay"},
        )
        assert comp_v3_2.status_code == 202
        assert comp_v3_1.json() == comp_v3_2.json()

        # New version v3 is now current
        asset_v3 = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_v3.json()["version_id"] == v3_id
        assert asset_v3.json()["file_name"] == "slides_v3.pdf"

        # 5. Earlier completion never rolls current version back:
        # Re-complete v1 (older version); logical asset must not roll back
        comp_older = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{v1_id}/complete",
            json={"checksum": cksum_1, "size_bytes": size_1},
            headers={"Idempotency-Key": "v1-replay"},
        )
        assert comp_older.status_code == 202
        assert comp_older.json()["version_id"] == v1_id
        # Current version is STILL v3
        asset_check_still_v3 = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_check_still_v3.json()["version_id"] == v3_id

        # 6. List and get versions
        v_list = await client.get(f"/api/v1/assets/{asset_id}/versions")
        assert v_list.status_code == 200
        items = v_list.json()["items"]
        assert len(items) == 3
        assert [item["version_number"] for item in items] == [1, 2, 3]

        v1_get = await client.get(f"/api/v1/assets/{asset_id}/versions/{v1_id}")
        assert v1_get.status_code == 200
        assert v1_get.json()["id"] == v1_id
        assert v1_get.json()["version_number"] == 1

        # 7. Version download intent allows downloading specific verified version
        v1_dl = await client.post(f"/api/v1/assets/{asset_id}/versions/{v1_id}/download-intents")
        assert v1_dl.status_code == 200
        assert v1_dl.json()["asset_version_id"] == v1_id

        # Rejected version cannot be downloaded
        v2_dl = await client.post(f"/api/v1/assets/{asset_id}/versions/{v2_id}/download-intents")
        assert v2_dl.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,url,payload",
    [
        (
            "POST",
            "/api/v1/assets/{aid}/versions/upload-intents",
            {
                "file_name": "x.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 100,
            },
        ),
        ("GET", "/api/v1/assets/{aid}/versions", None),
        ("GET", "/api/v1/assets/{aid}/versions/{vid}", None),
        ("POST", "/api/v1/assets/{aid}/versions/{vid}/download-intents", None),
        (
            "POST",
            "/api/v1/assets/{aid}/versions/{vid}/complete",
            {"checksum": "sha256:" + "0" * 64, "size_bytes": 100},
        ),
    ],
)
async def test_version_operations_conceal_outsiders_and_erased(
    member_user: User,
    outsider_user: User,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()

    norm_aid, norm_vid = uuid4(), uuid4()
    repo.assets[norm_aid] = Asset(
        norm_aid, PROJECT_ID, "supporting_document", "verified", "a.pdf", norm_vid, MEMBER_ID, NOW
    )
    repo.versions[norm_vid] = AssetVersion(
        norm_vid, norm_aid, 1, "verified", "key1", "a.pdf", "application/pdf", 100, MEMBER_ID, NOW
    )

    erased_aid, erased_vid = uuid4(), uuid4()
    repo.assets[erased_aid] = Asset(
        erased_aid,
        ERASED_PROJECT_ID,
        "supporting_document",
        "verified",
        "e.pdf",
        erased_vid,
        MEMBER_ID,
        NOW,
    )
    repo.versions[erased_vid] = AssetVersion(
        erased_vid,
        erased_aid,
        1,
        "verified",
        "key2",
        "e.pdf",
        "application/pdf",
        100,
        MEMBER_ID,
        NOW,
    )

    async with create_test_client(outsider_user, repo, storage) as outsider_client:
        target_url = url.format(aid=norm_aid, vid=norm_vid)
        kwargs: dict[str, Any] = {"headers": {"Idempotency-Key": "idem-out"}}
        if payload is not None:
            kwargs["json"] = payload
        res = await outsider_client.request(method, target_url, **kwargs)
        assert res.status_code == 404
        assert res.json()["code"] == "not_found"

    async with create_test_client(member_user, repo, storage) as member_client:
        target_url = url.format(aid=erased_aid, vid=erased_vid)
        kwargs = {"headers": {"Idempotency-Key": "idem-erase"}}
        if payload is not None:
            kwargs["json"] = payload
        res = await member_client.request(method, target_url, **kwargs)
        assert res.status_code == 404
        assert res.json()["code"] == "not_found"
