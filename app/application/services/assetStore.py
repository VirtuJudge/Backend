import json
import re
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from app.application.interfaces.assetRepository import AssetRepository
from app.application.interfaces.objectStorage import ObjectStoragePort
from app.application.interfaces.pdfVerifier import PdfVerifierPort
from app.domain.asset import (
    Asset,
    AssetCompletionConflict,
    AssetCorrupt,
    AssetIdempotencyConflict,
    AssetNotFound,
    AssetNotVerified,
    AssetSizeLimitExceeded,
    AssetUnsupportedMediaType,
    AssetValidationFailed,
    AssetVersion,
    DownloadIntent,
    StorageObjectNotFound,
    StorageUnavailable,
    UploadIntent,
)
from app.domain.idempotency import AssetUploadIdempotency

MAX_DOCUMENT_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_TTL_SECONDS = 900
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 3600
CHECKSUM_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")


def clamp_ttl(ttl: int) -> int:
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, ttl))


def compute_upload_fingerprint(
    kind: str,
    file_name: str,
    media_type: str,
    size_bytes: int,
) -> str:
    payload = {
        "file_name": file_name.strip(),
        "kind": kind,
        "media_type": media_type.strip().lower(),
        "size_bytes": size_bytes,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


class AssetStore:
    def __init__(
        self,
        repository: AssetRepository,
        storage: ObjectStoragePort,
        pdf_verifier: PdfVerifierPort,
        upload_ttl_seconds: int = DEFAULT_TTL_SECONDS,
        download_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.repository = repository
        self.storage = storage
        self.pdf_verifier = pdf_verifier
        self.upload_ttl_seconds = clamp_ttl(upload_ttl_seconds)
        self.download_ttl_seconds = clamp_ttl(download_ttl_seconds)

    async def _authorize_project(self, project_id: UUID, user_id: UUID) -> tuple[UUID, UUID]:
        project = await self.repository.get_project(project_id)
        if project is None:
            raise AssetNotFound
        if not await self.repository.is_team_member(project.team_id, user_id):
            raise AssetNotFound
        if await self.repository.is_project_erasure_requested(project_id):
            raise AssetNotFound
        return project.id, project.team_id

    async def _authorize_asset(self, asset_id: UUID, user_id: UUID) -> Asset:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise AssetNotFound
        await self._authorize_project(asset.project_id, user_id)
        return asset

    async def _reject_version(self, version: AssetVersion, asset: Asset, reason: str) -> None:
        version.state = "rejected"
        version.rejection_reason = reason
        asset.state = "rejected"
        asset.rejection_reason = reason
        await self.repository.save_version_rejection(version, asset)

    async def create_upload_intent(
        self,
        project_id: UUID,
        user_id: UUID,
        kind: str,
        file_name: str,
        declared_media_type: str,
        declared_size_bytes: int,
        idempotency_key: str,
    ) -> UploadIntent:
        _, team_id = await self._authorize_project(project_id, user_id)

        if kind != "supporting_document":
            raise AssetUnsupportedMediaType("Unsupported asset kind")

        normalized_name = file_name.strip()
        invalid_chars = ("/", "\\", "..")
        if not normalized_name or any(c in normalized_name for c in invalid_chars):
            raise AssetValidationFailed("Invalid file name")

        if not normalized_name.lower().endswith(".pdf"):
            raise AssetUnsupportedMediaType("File extension must be .pdf")

        normalized_media_type = declared_media_type.strip().lower()
        if normalized_media_type != "application/pdf":
            raise AssetUnsupportedMediaType("Declared media type must be application/pdf")

        if declared_size_bytes <= 0:
            raise AssetValidationFailed("Declared size must be positive")
        if declared_size_bytes > MAX_DOCUMENT_SIZE_BYTES:
            raise AssetSizeLimitExceeded("Declared size exceeds 25 MiB limit")

        request_hash = compute_upload_fingerprint(
            kind,
            normalized_name,
            normalized_media_type,
            declared_size_bytes,
        )

        previous = await self.repository.get_upload_idempotency(
            user_id, project_id, "upload_intent", idempotency_key
        )
        if previous is not None:
            if previous.request_hash != request_hash:
                raise AssetIdempotencyConflict(
                    "Idempotency key already used with different parameters"
                )
            existing_version = await self.repository.get_version(previous.version_id)
            if existing_version is not None:
                upload_url, headers, expires_at = self.storage.generate_upload_url(
                    existing_version.storage_key,
                    existing_version.declared_media_type,
                    existing_version.declared_size_bytes,
                    self.upload_ttl_seconds,
                )
                return UploadIntent(
                    asset_id=existing_version.asset_id,
                    asset_version_id=existing_version.id,
                    upload_url=upload_url,
                    method="PUT",
                    required_headers=headers,
                    expires_at=expires_at,
                    maximum_size_bytes=MAX_DOCUMENT_SIZE_BYTES,
                )

        asset_id = uuid4()
        version_id = uuid4()
        now = datetime.now(UTC)
        storage_key = f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{version_id}.pdf"

        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind=kind,
            state="pending_upload",
            file_name=normalized_name,
            current_version_id=version_id,
            created_by=user_id,
            created_at=now,
        )
        version = AssetVersion(
            id=version_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key=storage_key,
            file_name=normalized_name,
            declared_media_type=normalized_media_type,
            declared_size_bytes=declared_size_bytes,
            created_by=user_id,
            created_at=now,
        )
        idempotency = AssetUploadIdempotency(
            user_id=user_id,
            project_id=project_id,
            operation="upload_intent",
            key=idempotency_key,
            request_hash=request_hash,
            asset_id=asset_id,
            version_id=version_id,
        )

        saved_asset, saved_version = await self.repository.save_asset_with_initial_version(
            asset, version, idempotency
        )

        upload_url, headers, expires_at = self.storage.generate_upload_url(
            saved_version.storage_key,
            saved_version.declared_media_type,
            saved_version.declared_size_bytes,
            self.upload_ttl_seconds,
        )

        return UploadIntent(
            asset_id=saved_asset.id,
            asset_version_id=saved_version.id,
            upload_url=upload_url,
            method="PUT",
            required_headers=headers,
            expires_at=expires_at,
            maximum_size_bytes=MAX_DOCUMENT_SIZE_BYTES,
        )

    async def complete_upload(
        self,
        asset_id: UUID,
        version_id: UUID,
        user_id: UUID,
        checksum: str,
        size_bytes: int,
        idempotency_key: str,
    ) -> Asset:
        asset = await self._authorize_asset(asset_id, user_id)

        if not CHECKSUM_REGEX.match(checksum):
            raise AssetValidationFailed("Checksum must be in sha256:<64 lowercase hex> format")
        if size_bytes <= 0:
            raise AssetValidationFailed("Size must be positive")

        version = await self.repository.get_version_for_update(version_id)
        if version is None or version.asset_id != asset_id:
            raise AssetNotFound

        if version.state == "verified":
            if version.checksum == checksum and version.size_bytes == size_bytes:
                latest_asset = await self.repository.get_asset(asset_id)
                return latest_asset or asset
            raise AssetCompletionConflict("Checksum or size does not match verified version")

        if version.state == "rejected":
            raise AssetCompletionConflict("Asset version has already been rejected")

        if size_bytes != version.declared_size_bytes:
            await self._reject_version(version, asset, "size_mismatch")
            raise AssetCorrupt("size_mismatch")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        try:
            try:
                (
                    observed_bytes,
                    observed_checksum,
                    observed_type,
                ) = await self.storage.stream_to_disk(
                    version.storage_key,
                    temp_path,
                    MAX_DOCUMENT_SIZE_BYTES,
                )
            except (StorageUnavailable, StorageObjectNotFound):
                raise
            except AssetSizeLimitExceeded as err:
                await self._reject_version(version, asset, "file_size_exceeded")
                raise AssetSizeLimitExceeded("file_size_exceeded") from err

            if observed_type.strip().lower() != version.declared_media_type:
                await self._reject_version(version, asset, "media_type_mismatch")
                raise AssetCorrupt("media_type_mismatch")

            if observed_bytes != version.declared_size_bytes or observed_bytes != size_bytes:
                await self._reject_version(version, asset, "size_mismatch")
                raise AssetCorrupt("size_mismatch")

            formatted_checksum = f"sha256:{observed_checksum}"
            if formatted_checksum != checksum:
                await self._reject_version(version, asset, "checksum_mismatch")
                raise AssetCorrupt("checksum_mismatch")

            try:
                await self.pdf_verifier.verify_pdf(temp_path)
            except AssetCorrupt as err:
                await self._reject_version(version, asset, err.reason)
                raise

            now = datetime.now(UTC)
            version.state = "verified"
            version.size_bytes = observed_bytes
            version.checksum = formatted_checksum
            version.media_type = "application/pdf"
            version.completed_at = now

            asset.state = "verified"
            asset.size_bytes = observed_bytes
            asset.checksum = formatted_checksum
            asset.media_type = "application/pdf"
            asset.current_version_id = version.id

            await self.repository.save_version_completion(version, asset)
            return asset
        finally:
            temp_path.unlink(missing_ok=True)

    async def get_asset(self, asset_id: UUID, user_id: UUID) -> Asset:
        return await self._authorize_asset(asset_id, user_id)

    async def list_assets(
        self,
        project_id: UUID,
        user_id: UUID,
        cursor: UUID | None,
        limit: int,
        kind: str | None = None,
        state: str | None = None,
    ) -> tuple[list[Asset], UUID | None]:
        await self._authorize_project(project_id, user_id)
        return await self.repository.list_assets(project_id, cursor, limit, kind, state)

    async def create_download_intent(self, asset_id: UUID, user_id: UUID) -> DownloadIntent:
        asset = await self._authorize_asset(asset_id, user_id)
        if asset.state != "verified" or asset.current_version_id is None:
            raise AssetNotVerified("Asset is not in verified state")

        version = await self.repository.get_version(asset.current_version_id)
        if version is None:
            raise AssetNotFound

        download_url, expires_at = self.storage.generate_download_url(
            version.storage_key, self.download_ttl_seconds
        )

        return DownloadIntent(
            asset_id=asset.id,
            asset_version_id=version.id,
            download_url=download_url,
            expires_at=expires_at,
            media_type=asset.media_type or "application/pdf",
            size_bytes=asset.size_bytes or 0,
            file_name=asset.file_name,
        )
