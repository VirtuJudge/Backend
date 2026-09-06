import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter

from app.application.interfaces.assetRepository import AssetRepository
from app.application.interfaces.objectStorage import ObjectStoragePort
from app.application.services.assetStore import AssetStore
from app.domain.asset import (
    Asset,
    AssetSizeLimitExceeded,
    AssetVersion,
    StorageObjectNotFound,
    StorageUnavailable,
)
from app.domain.idempotency import AssetUploadIdempotency
from app.domain.project import Project
from app.domain.user import User
from app.infrastructure.pdf.pypdfVerifier import PyPdfVerifier
from app.infrastructure.settings import Settings
from app.main import create_app

TEAM_ID = uuid4()
PROJECT_ID = uuid4()
ERASED_PROJECT_ID = uuid4()
MEMBER_ID = uuid4()
OUTSIDER_ID = uuid4()
NOW = datetime.now(UTC)


def make_pdf(encrypted: bool = False, password: str = "pass") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt(password)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class FakeAssetRepository(AssetRepository):
    def __init__(self) -> None:
        self.projects: dict[UUID, Project] = {
            PROJECT_ID: Project(PROJECT_ID, TEAM_ID, "Active Project", None, NOW),
            ERASED_PROJECT_ID: Project(ERASED_PROJECT_ID, TEAM_ID, "Erased Project", None, NOW),
        }
        self.erased_projects: set[UUID] = {ERASED_PROJECT_ID}
        self.team_members: set[tuple[UUID, UUID]] = {(TEAM_ID, MEMBER_ID)}
        self.assets: dict[UUID, Asset] = {}
        self.versions: dict[UUID, AssetVersion] = {}
        self.idempotency: dict[tuple[UUID, UUID, str, str], AssetUploadIdempotency] = {}

    async def get_project(self, project_id: UUID) -> Project | None:
        return self.projects.get(project_id)

    async def is_team_member(self, team_id: UUID, user_id: UUID) -> bool:
        return (team_id, user_id) in self.team_members

    async def is_project_erasure_requested(self, project_id: UUID) -> bool:
        return project_id in self.erased_projects

    async def get_upload_idempotency(
        self, user_id: UUID, project_id: UUID, operation: str, key: str
    ) -> AssetUploadIdempotency | None:
        return self.idempotency.get((user_id, project_id, operation, key))

    async def save_asset_with_initial_version(
        self,
        asset: Asset,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        self.assets[asset.id] = asset
        self.versions[version.id] = version
        self.idempotency[
            (idempotency.user_id, idempotency.project_id, idempotency.operation, idempotency.key)
        ] = idempotency
        return asset, version

    async def get_asset(self, asset_id: UUID) -> Asset | None:
        return self.assets.get(asset_id)

    async def get_version(self, version_id: UUID) -> AssetVersion | None:
        return self.versions.get(version_id)

    async def get_version_for_update(self, version_id: UUID) -> AssetVersion | None:
        return self.versions.get(version_id)

    async def save_version_completion(self, version: AssetVersion, asset: Asset) -> None:
        self.versions[version.id] = version
        self.assets[asset.id] = asset

    async def save_version_rejection(self, version: AssetVersion, asset: Asset) -> None:
        self.versions[version.id] = version
        self.assets[asset.id] = asset

    async def list_assets(
        self,
        project_id: UUID,
        cursor: UUID | None,
        limit: int,
        kind: str | None,
        state: str | None,
    ) -> tuple[list[Asset], UUID | None]:
        items = [
            a
            for a in self.assets.values()
            if a.project_id == project_id
            and (kind is None or a.kind == kind)
            and (state is None or a.state == state)
        ]
        items.sort(key=lambda x: str(x.id))
        if cursor is not None:
            items = [a for a in items if str(a.id) > str(cursor)]
        has_more = len(items) > limit
        page = items[:limit]
        next_cursor = page[-1].id if has_more and page else None
        return page, next_cursor


class FakeObjectStorage(ObjectStoragePort):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.transient_failure = False
        self.signing_failure = False

    def generate_upload_url(
        self,
        storage_key: str,
        content_type: str,
        content_length: int,
        ttl_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        if self.signing_failure:
            raise StorageUnavailable("Signing unavailable")
        url = (
            f"https://storage.example/{storage_key}?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-SignedHeaders=content-length%3Bcontent-type%3Bhost%3Bif-none-match"
        )
        headers = {"content-type": content_type, "if-none-match": "*"}
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        return url, headers, expires

    def generate_download_url(
        self,
        storage_key: str,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        if self.signing_failure:
            raise StorageUnavailable("Signing unavailable")
        url = f"https://storage.example/{storage_key}?X-Amz-Signature=fake-signature"
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        return url, expires

    async def stream_to_disk(
        self,
        storage_key: str,
        target_path: Path,
        max_bytes: int,
    ) -> tuple[int, str, str]:
        if self.transient_failure:
            raise StorageUnavailable("Storage transient network timeout")

        if storage_key not in self.objects:
            raise StorageObjectNotFound("Key does not exist in storage")

        data = self.objects[storage_key]
        if len(data) > max_bytes:
            raise AssetSizeLimitExceeded("Object size exceeds maximum limit")

        target_path.write_bytes(data)
        hasher = hashlib.sha256(data)
        return len(data), hasher.hexdigest(), "application/pdf"


class FakeTokenVerifier:
    def __init__(self, user: User):
        self.user = user

    def verify(self, token: str) -> dict[str, Any]:
        return {
            "iss": self.user.issuer,
            "sub": self.user.subject,
            "email": self.user.email,
        }


class FakeUserService:
    def __init__(self, user: User):
        self.user = user

    async def get_or_create_user(self, issuer: str, subject: str, email: str | None = None) -> User:
        return self.user


@pytest.fixture
def member_user() -> User:
    return User(
        id=MEMBER_ID,
        issuer="https://auth.example",
        subject="member-sub",
        email="member@example.com",
        created_at=NOW,
    )


@pytest.fixture
def outsider_user() -> User:
    return User(
        id=OUTSIDER_ID,
        issuer="https://auth.example",
        subject="outsider-sub",
        email="outsider@example.com",
        created_at=NOW,
    )


def create_test_client(
    user: User,
    repo: AssetRepository,
    storage: ObjectStoragePort,
) -> AsyncClient:
    settings = Settings(
        app_env="test",
        oidc_issuer="https://auth.example",
        oidc_audience="test",
        oidc_jwks_url="https://auth.example/.well-known/jwks.json",
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
    )
    application = create_app(settings)
    application.state.token_verifier = FakeTokenVerifier(user)
    application.state.user_service_factory = lambda session: FakeUserService(user)

    asset_store = AssetStore(
        repository=repo,
        storage=storage,
        pdf_verifier=PyPdfVerifier(),
        upload_ttl_seconds=900,
        download_ttl_seconds=900,
    )
    application.state.asset_store_factory = lambda session: asset_store

    async def mock_session(request: Any = None) -> AsyncIterator[None]:
        yield None

    application.state.session_dependency = mock_session

    transport = ASGITransport(app=application)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer fake-token"},
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
