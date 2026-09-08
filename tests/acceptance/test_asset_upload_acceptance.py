import pytest

from app.domain.user import User
from tests.support import (
    ERASED_PROJECT_ID,
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    create_test_client,
)


@pytest.mark.anyio
async def test_create_upload_intent_happy_path(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        payload = {
            "kind": "supporting_document",
            "file_name": "presentation.pdf",
            "declared_media_type": "application/pdf",
            "declared_size_bytes": 1024,
        }
        res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json=payload,
            headers={"Idempotency-Key": "upload-key-1"},
        )
        assert res.status_code == 201
        data = res.json()
        assert "asset_id" in data
        assert "asset_version_id" in data
        assert data["upload_url"].startswith("https://storage.example/")
        assert data["method"] == "PUT"
        assert data["required_headers"]["content-type"] == "application/pdf"
        assert data["required_headers"]["if-none-match"] == "*"
        assert data["maximum_size_bytes"] == 25 * 1024 * 1024


@pytest.mark.anyio
async def test_create_upload_intent_conceals_outsiders(outsider_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(outsider_user, repo, storage) as client:
        payload = {
            "kind": "supporting_document",
            "file_name": "doc.pdf",
            "declared_media_type": "application/pdf",
            "declared_size_bytes": 1024,
        }
        res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json=payload,
            headers={"Idempotency-Key": "key-1"},
        )
        assert res.status_code == 404
        assert res.headers["content-type"] == "application/problem+json"
        assert res.json()["code"] == "not_found"


@pytest.mark.anyio
async def test_create_upload_intent_conceals_erased_project(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        payload = {
            "kind": "supporting_document",
            "file_name": "doc.pdf",
            "declared_media_type": "application/pdf",
            "declared_size_bytes": 1024,
        }
        res = await client.post(
            f"/api/v1/projects/{ERASED_PROJECT_ID}/assets/upload-intents",
            json=payload,
            headers={"Idempotency-Key": "key-1"},
        )
        assert res.status_code == 404
        assert res.headers["content-type"] == "application/problem+json"
        assert res.json()["code"] == "not_found"


@pytest.mark.anyio
async def test_create_upload_intent_validation_errors(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        # 1. Invalid kind -> 415 Unsupported Media Type
        res1 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "presentation_video",
                "file_name": "doc.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 1024,
            },
            headers={"Idempotency-Key": "k1"},
        )
        assert res1.status_code == 415
        assert res1.headers["content-type"] == "application/problem+json"
        assert res1.json()["code"] == "unsupported_media_type"

        # 2. Non-pdf extension -> 415 Unsupported Media Type
        res2 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc.png",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 1024,
            },
            headers={"Idempotency-Key": "k2"},
        )
        assert res2.status_code == 415
        assert res2.headers["content-type"] == "application/problem+json"
        assert res2.json()["code"] == "unsupported_media_type"

        # 3. Media type mismatch -> 415 Unsupported Media Type
        res3 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc.pdf",
                "declared_media_type": "image/png",
                "declared_size_bytes": 1024,
            },
            headers={"Idempotency-Key": "k3"},
        )
        assert res3.status_code == 415
        assert res3.headers["content-type"] == "application/problem+json"
        assert res3.json()["code"] == "unsupported_media_type"

        # 4. Zero/negative size -> 422 Validation Failed
        res4 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 0,
            },
            headers={"Idempotency-Key": "k4"},
        )
        assert res4.status_code == 422
        assert res4.headers["content-type"] == "application/problem+json"
        assert res4.json()["code"] == "validation_failed"

        # 5. Exceeding 25 MiB limit -> 413 Payload Too Large
        res5 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "doc.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 26 * 1024 * 1024,
            },
            headers={"Idempotency-Key": "k5"},
        )
        assert res5.status_code == 413
        assert res5.headers["content-type"] == "application/problem+json"
        assert res5.json()["code"] == "payload_too_large"


@pytest.mark.anyio
async def test_create_upload_intent_idempotency_and_conflict(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    async with create_test_client(member_user, repo, storage) as client:
        payload = {
            "kind": "supporting_document",
            "file_name": "doc.pdf",
            "declared_media_type": "application/pdf",
            "declared_size_bytes": 1024,
        }
        res1 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json=payload,
            headers={"Idempotency-Key": "idem-1"},
        )
        assert res1.status_code == 201
        data1 = res1.json()

        # Replay identical payload -> same result
        res2 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json=payload,
            headers={"Idempotency-Key": "idem-1"},
        )
        assert res2.status_code == 201
        data2 = res2.json()
        assert data1["asset_id"] == data2["asset_id"]
        assert data1["asset_version_id"] == data2["asset_version_id"]

        # Replay with changed payload -> 409 conflict
        conflicting_payload = {**payload, "file_name": "other.pdf"}
        res3 = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json=conflicting_payload,
            headers={"Idempotency-Key": "idem-1"},
        )
        assert res3.status_code == 409
        assert res3.headers["content-type"] == "application/problem+json"
        assert res3.json()["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_storage_signing_failure_returns_503(member_user: User) -> None:
    repo = FakeAssetRepository()
    storage = FakeObjectStorage()
    storage.signing_failure = True
    async with create_test_client(member_user, repo, storage) as client:
        upload_res = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents",
            json={
                "kind": "supporting_document",
                "file_name": "sign_err.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": 1000,
            },
            headers={"Idempotency-Key": "sign-fail-1"},
        )
        assert upload_res.status_code == 503
        assert upload_res.headers["content-type"] == "application/problem+json"
        assert upload_res.json()["code"] == "service_unavailable"
