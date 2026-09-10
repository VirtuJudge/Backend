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
async def test_complete_upload_happy_path(member_user: User) -> None:
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
                "file_name": "valid.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size,
            },
            headers={"Idempotency-Key": "key-comp-1"},
        )
        assert init_res.status_code == 201
        init_data = init_res.json()
        asset_id = init_data["asset_id"]
        version_id = init_data["asset_version_id"]

        version = await repo.get_version(UUID(version_id))
        assert version is not None
        storage.objects[version.storage_key] = pdf_bytes

        comp_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-key-1"},
        )
        assert comp_res.status_code == 202
        data = comp_res.json()
        assert data["state"] == "verified"
        assert data["size_bytes"] == size
        assert data["checksum"] == checksum

        # Repeated completion with same checksum/size succeeds
        repeat_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "different-key"},
        )
        assert repeat_res.status_code == 202
        assert repeat_res.json()["state"] == "verified"

        # Repeated completion with mismatching checksum/size raises 409 Problem Details
        mismatch_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": f"sha256:{'a' * 64}", "size_bytes": size},
            headers={"Idempotency-Key": "key-mismatch"},
        )
        assert mismatch_res.status_code == 409
        assert mismatch_res.headers["content-type"] == "application/problem+json"


@pytest.mark.anyio
async def test_complete_upload_corrupt_pdf_persists_rejection(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        corrupt_bytes = b"%PDF-1.4\ngarbage unreadable structure"
        size = len(corrupt_bytes)
        checksum = f"sha256:{hashlib.sha256(corrupt_bytes).hexdigest()}"

        init_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "corrupt.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size,
            },
            headers={"Idempotency-Key": "corrupt-1"},
        )
        asset_id = init_res.json()["asset_id"]
        version_id = init_res.json()["asset_version_id"]

        version = await repo.get_version(UUID(version_id))
        assert version is not None
        storage.objects[version.storage_key] = corrupt_bytes

        comp_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-corrupt"},
        )
        assert comp_res.status_code == 422
        assert comp_res.headers["content-type"] == "application/problem+json"
        assert comp_res.json()["code"] == "corrupt_pdf"

        # Verify rejection is safely persisted in DB
        asset_check = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_check.status_code == 200
        assert asset_check.json()["state"] == "rejected"
        assert asset_check.json()["rejection_reason"] == "corrupt_pdf"

        # Retrying completion on already rejected version raises 409 Problem Details
        retry_comp = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-corrupt-retry"},
        )
        assert retry_comp.status_code == 409
        assert retry_comp.headers["content-type"] == "application/problem+json"


@pytest.mark.anyio
async def test_complete_upload_encrypted_pdf_persists_rejection(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        enc_bytes = make_pdf(encrypted=True)
        size = len(enc_bytes)
        checksum = f"sha256:{hashlib.sha256(enc_bytes).hexdigest()}"

        init_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "enc.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size,
            },
            headers={"Idempotency-Key": "enc-1"},
        )
        asset_id = init_res.json()["asset_id"]
        version_id = init_res.json()["asset_version_id"]

        version = await repo.get_version(UUID(version_id))
        assert version is not None
        storage.objects[version.storage_key] = enc_bytes

        comp_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-enc"},
        )
        assert comp_res.status_code == 422
        assert comp_res.headers["content-type"] == "application/problem+json"
        assert comp_res.json()["code"] == "encrypted_pdf"

        asset_check = await client.get(f"/api/v1/assets/{asset_id}")
        assert asset_check.json()["state"] == "rejected"
        assert asset_check.json()["rejection_reason"] == "encrypted_pdf"


@pytest.mark.anyio
async def test_complete_upload_storage_transient_failure_does_not_reject(
    member_user: User,
) -> None:
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
                "file_name": "retry.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": size,
            },
            headers={"Idempotency-Key": "retry-1"},
        )
        asset_id = init_res.json()["asset_id"]
        version_id = init_res.json()["asset_version_id"]

        version = await repo.get_version(UUID(version_id))
        assert version is not None
        storage.objects[version.storage_key] = pdf_bytes
        storage.transient_failure = True

        comp_res = await client.post(
            f"/api/v1/assets/{asset_id}/versions/{version_id}/complete",
            json={"checksum": checksum, "size_bytes": size},
            headers={"Idempotency-Key": "comp-retry"},
        )
        assert comp_res.status_code == 503
        assert comp_res.headers["content-type"] == "application/problem+json"
        assert comp_res.json()["code"] == "service_unavailable"

        version_after = await repo.get_version(UUID(version_id))
        assert version_after is not None
        assert version_after.state == "pending_upload"
