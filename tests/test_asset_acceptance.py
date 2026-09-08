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
from app.application.interfaces.mediaVerifier import MediaVerifierPort
from app.application.interfaces.objectStorage import ObjectStoragePort
from app.application.services.assetStore import AssetStore
from app.domain.asset import (
    Asset,
    AssetIdempotencyConflict,
    AssetNotFound,
    AssetSizeLimitExceeded,
    AssetVersion,
    StorageObjectNotFound,
    StorageUnavailable,
)
from app.domain.idempotency import AssetUploadIdempotency
from app.domain.project import Project
from app.domain.user import User
from app.infrastructure.documents.document_verifier import DocumentVerifier
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

    async def save_replacement_version(
        self,
        asset_id: UUID,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        asset = self.assets.get(asset_id)
        if asset is None:
            raise AssetNotFound
        existing_idempotency = self.idempotency.get(
            (idempotency.user_id, idempotency.project_id, idempotency.operation, idempotency.key)
        )
        if existing_idempotency is not None:
            if existing_idempotency.request_hash != idempotency.request_hash:
                raise AssetIdempotencyConflict(
                    "Idempotency key already used with different parameters"
                )
            existing_ver = self.versions.get(existing_idempotency.version_id)
            if existing_ver is not None:
                return asset, existing_ver

        existing_versions = [v for v in self.versions.values() if v.asset_id == asset_id]
        next_v_num = max([v.version_number for v in existing_versions], default=0) + 1
        version.version_number = next_v_num
        self.versions[version.id] = version
        self.idempotency[
            (idempotency.user_id, idempotency.project_id, idempotency.operation, idempotency.key)
        ] = idempotency
        return asset, version

    async def get_asset_and_version_for_completion(
        self, asset_id: UUID, version_id: UUID
    ) -> tuple[Asset | None, AssetVersion | None, AssetVersion | None]:
        asset = self.assets.get(asset_id)
        if asset is None:
            return None, None, None
        version = self.versions.get(version_id)
        if version is None or version.asset_id != asset_id:
            return asset, None, None
        current_ver = (
            self.versions.get(asset.current_version_id)
            if asset.current_version_id is not None
            else None
        )
        return asset, version, current_ver

    async def list_versions(
        self,
        asset_id: UUID,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[AssetVersion], UUID | None]:
        items = [v for v in self.versions.values() if v.asset_id == asset_id]
        items.sort(key=lambda x: (x.version_number, str(x.id)))
        if cursor is not None:
            cursor_idx = -1
            for idx, v in enumerate(items):
                if v.id == cursor:
                    cursor_idx = idx
                    break
            if cursor_idx >= 0:
                items = items[cursor_idx + 1 :]
        has_more = len(items) > limit
        page = items[:limit]
        next_cursor = page[-1].id if has_more and page else None
        return page, next_cursor

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
            and (a.state == state if state is not None else a.state != "deleted")
        ]
        items.sort(key=lambda x: str(x.id))
        if cursor is not None:
            items = [a for a in items if str(a.id) > str(cursor)]
        has_more = len(items) > limit
        page = items[:limit]
        next_cursor = page[-1].id if has_more and page else None
        return page, next_cursor

    async def find_cleanup_candidate_versions(
        self,
        cutoff_created_at: datetime,
        cutoff_expires_at: datetime,
        now: datetime,
        limit: int,
    ) -> list[tuple[UUID, UUID]]:
        candidates = []
        for v in self.versions.values():
            if (
                v.state in ("pending_upload", "rejected", "deleting", "deleted")
                and v.upload_expires_at is not None
                and v.upload_expires_at <= cutoff_expires_at
                and v.created_at <= cutoff_created_at
                and (v.cleanup_next_attempt_at is None or v.cleanup_next_attempt_at <= now)
            ):
                candidates.append(v)
        candidates.sort(
            key=lambda v: (
                v.cleanup_next_attempt_at is not None,
                v.cleanup_next_attempt_at or datetime.min.replace(tzinfo=UTC),
                v.created_at,
            )
        )
        return [(v.asset_id, v.id) for v in candidates[:limit]]

    async def claim_version_for_cleanup(
        self,
        asset_id: UUID,
        version_id: UUID,
        now: datetime,
        lease_seconds: int,
        tombstone_delay_seconds: int,
        cutoff_created_at: datetime,
        cutoff_expires_at: datetime,
    ) -> AssetVersion | None:
        asset = self.assets.get(asset_id)
        version = self.versions.get(version_id)
        if asset is None or version is None or version.asset_id != asset_id:
            return None
        if version.state not in ("pending_upload", "rejected", "deleting", "deleted"):
            return None
        if version.upload_expires_at is not None and version.upload_expires_at > cutoff_expires_at:
            return None
        if version.created_at > cutoff_created_at:
            return None
        if version.cleanup_next_attempt_at is not None and version.cleanup_next_attempt_at > now:
            return None

        if version.state in ("pending_upload", "rejected", "deleting"):
            version.state = "deleting"
            version.cleanup_next_attempt_at = now + timedelta(seconds=lease_seconds)
        elif version.state == "deleted":
            version.cleanup_next_attempt_at = now + timedelta(seconds=tombstone_delay_seconds)

        return version

    async def record_cleanup_failure(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        version = self.versions.get(version_id)
        if version is not None:
            version.cleanup_next_attempt_at = next_attempt_at

    async def finalize_version_cleanup(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        version = self.versions.get(version_id)
        if version is not None:
            version.state = "deleted"
            version.cleanup_next_attempt_at = next_attempt_at

        asset = self.assets.get(asset_id)
        if asset is not None and asset.current_version_id == version_id:
            verified_versions = [
                v
                for v in self.versions.values()
                if v.asset_id == asset_id and v.state == "verified" and v.id != version_id
            ]
            if verified_versions:
                verified_versions.sort(key=lambda x: x.version_number, reverse=True)
                latest_verified = verified_versions[0]
                asset.current_version_id = latest_verified.id
                asset.state = "verified"
                asset.file_name = latest_verified.file_name
                asset.media_type = latest_verified.media_type
                asset.size_bytes = latest_verified.size_bytes
                asset.checksum = latest_verified.checksum
                asset.duration_ms = latest_verified.duration_ms
            else:
                pending_versions = [
                    v
                    for v in self.versions.values()
                    if v.asset_id == asset_id and v.state == "pending_upload" and v.id != version_id
                ]
                if pending_versions:
                    pending_versions.sort(key=lambda x: x.version_number, reverse=True)
                    latest_pending = pending_versions[0]
                    asset.current_version_id = latest_pending.id
                    asset.state = "pending_upload"
                    asset.file_name = latest_pending.file_name
                    asset.media_type = None
                    asset.size_bytes = None
                    asset.checksum = None
                    asset.duration_ms = None
                    asset.rejection_reason = None
                else:
                    asset.state = "deleted"


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
        ext = storage_key.rsplit(".", 1)[-1].lower()
        m_type = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if ext == "pptx"
            else "application/pdf"
        )
        return len(data), hasher.hexdigest(), m_type

    async def delete_object(self, storage_key: str) -> None:
        if self.transient_failure:
            raise StorageUnavailable("Storage transient network timeout")
        self.objects.pop(storage_key, None)


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

    async def get_or_create_user(
        self,
        issuer: str,
        subject: str,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
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
    media_verifier: MediaVerifierPort | None = None,
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
        document_verifier=DocumentVerifier(),
        media_verifier=media_verifier,
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
