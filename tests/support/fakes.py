import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.application.ports.asset_repository import AssetRepository
from app.application.ports.object_storage import ObjectStoragePort
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
from tests.support.constants import ERASED_PROJECT_ID, MEMBER_ID, NOW, PROJECT_ID, TEAM_ID
from tests.support.fake_ai_job_queue import (
    FakeAIJobQueue,
    FakeAIQueue,
    RecordingAIJobQueue,
    RecordingAIQueue,
)


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

    async def get_object(self, storage_key: str, max_bytes: int) -> bytes:
        if self.transient_failure:
            raise StorageUnavailable("Storage transient network timeout")
        if storage_key not in self.objects:
            raise StorageObjectNotFound("Key does not exist in storage")

        data = self.objects[storage_key]
        if len(data) > max_bytes:
            raise AssetSizeLimitExceeded("Object size exceeds maximum limit")
        return data

    async def delete_object(self, storage_key: str) -> None:
        if self.transient_failure:
            raise StorageUnavailable("Storage transient network timeout")
        self.objects.pop(storage_key, None)

    async def put_object(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.transient_failure:
            raise StorageUnavailable("Storage transient network timeout")
        self.objects[storage_key] = data


class RecordingStorage(FakeObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.deleted_keys: list[str] = []
        self.last_ttl_seconds: int | None = None
        self.fail_delete = False

    def generate_upload_url(
        self,
        storage_key: str,
        content_type: str,
        content_length: int,
        ttl_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        self.last_ttl_seconds = ttl_seconds
        return super().generate_upload_url(storage_key, content_type, content_length, ttl_seconds)

    async def delete_object(self, storage_key: str) -> None:
        if self.fail_delete:
            raise StorageUnavailable("Simulated storage failure during delete")
        self.deleted_keys.append(storage_key)
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


__all__ = [
    "FakeAssetRepository",
    "FakeObjectStorage",
    "RecordingStorage",
    "FakeTokenVerifier",
    "FakeUserService",
    "FakeAIJobQueue",
    "RecordingAIJobQueue",
    "FakeAIQueue",
    "RecordingAIQueue",
]
