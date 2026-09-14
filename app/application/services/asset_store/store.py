import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.application.ports.asset_repository import AssetRepository
from app.application.ports.document_verifier import DocumentVerifierPort
from app.application.ports.media_verifier import MediaVerifierPort
from app.application.ports.object_storage import ObjectStoragePort
from app.application.services.asset_store.fingerprint import (
    ALLOWED_ASSET_TYPES,
    ASSET_KIND_LIMITS,
    CHECKSUM_REGEX,
    DEFAULT_TTL_SECONDS,
    MAX_DOCUMENT_SIZE_BYTES,
    clamp_ttl,
    compute_upload_fingerprint,
    to_utc,
    validate_asset_file_and_type,
)
from app.domain.asset import (
    Asset,
    AssetCompletionConflict,
    AssetCorrupt,
    AssetIdempotencyConflict,
    AssetNotFound,
    AssetNotVerified,
    AssetReplacementNotAllowed,
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

logger = logging.getLogger(__name__)


class AssetStore:
    def __init__(
        self,
        repository: AssetRepository,
        storage: ObjectStoragePort,
        document_verifier: DocumentVerifierPort,
        media_verifier: MediaVerifierPort | None = None,
        upload_ttl_seconds: int = DEFAULT_TTL_SECONDS,
        download_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.repository = repository
        self.storage = storage
        self.document_verifier = document_verifier
        self.media_verifier = media_verifier
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
        if asset is None or asset.state == "deleted":
            raise AssetNotFound
        await self._authorize_project(asset.project_id, user_id)
        return asset

    async def _reject_version(
        self,
        version: AssetVersion,
        asset: Asset,
        rejection_reason: str,
    ) -> None:
        version.state = "rejected"
        version.rejection_reason = rejection_reason
        if asset.current_version_id == version.id:
            asset.state = "rejected"
            asset.rejection_reason = rejection_reason
        await self.repository.save_version_rejection(version, asset)

    def _build_upload_intent(
        self,
        asset_id: UUID,
        version: AssetVersion,
        max_size_bytes: int,
    ) -> UploadIntent:
        now = datetime.now(UTC)
        if version.state != "pending_upload":
            raise AssetIdempotencyConflict(
                f"Cannot replay upload intent for version in state '{version.state}'"
            )
        exp_utc = to_utc(version.upload_expires_at)
        if exp_utc is not None and exp_utc <= now:
            raise AssetIdempotencyConflict("Upload intent has expired")
        remaining_seconds = (
            int((exp_utc - now).total_seconds()) if exp_utc else self.upload_ttl_seconds
        )
        if remaining_seconds <= 0:
            raise AssetIdempotencyConflict("Upload intent has expired")

        signing_ttl = min(self.upload_ttl_seconds, remaining_seconds)

        url, headers, _ = self.storage.generate_upload_url(
            storage_key=version.storage_key,
            content_type=version.declared_media_type,
            content_length=version.declared_size_bytes,
            ttl_seconds=signing_ttl,
        )
        return UploadIntent(
            asset_id=asset_id,
            asset_version_id=version.id,
            upload_url=url,
            method="PUT",
            required_headers=headers,
            expires_at=exp_utc or (now + timedelta(seconds=remaining_seconds)),
            maximum_size_bytes=max_size_bytes,
        )

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

        if kind not in ALLOWED_ASSET_TYPES:
            raise AssetUnsupportedMediaType("Unsupported asset kind")

        normalized_name, canonical_media_type, ext = validate_asset_file_and_type(
            kind, file_name, declared_media_type
        )

        kind_limit = ASSET_KIND_LIMITS[kind]
        max_size_bytes = kind_limit["max_size_bytes"]
        assert max_size_bytes is not None

        if declared_size_bytes <= 0:
            raise AssetValidationFailed("Declared size must be positive")
        if declared_size_bytes > max_size_bytes:
            max_mb = max_size_bytes // (1024 * 1024)
            raise AssetSizeLimitExceeded(f"Declared size exceeds {max_mb} MiB limit")

        request_hash = compute_upload_fingerprint(
            kind,
            normalized_name,
            canonical_media_type,
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
                return self._build_upload_intent(
                    existing_version.asset_id, existing_version, max_size_bytes
                )

        asset_id = uuid4()
        version_id = uuid4()
        now = datetime.now(UTC)
        upload_expires_at = now + timedelta(seconds=self.upload_ttl_seconds)
        storage_key = f"teams/{team_id}/projects/{project_id}/assets/{asset_id}/{version_id}{ext}"
        retention_expires_at = (
            now + timedelta(days=30) if kind in ("presentation_video", "answer_audio") else None
        )

        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind=kind,
            state="pending_upload",
            file_name=normalized_name,
            current_version_id=version_id,
            created_by=user_id,
            created_at=now,
            retention_expires_at=retention_expires_at,
        )
        version = AssetVersion(
            id=version_id,
            asset_id=asset_id,
            version_number=1,
            state="pending_upload",
            storage_key=storage_key,
            file_name=normalized_name,
            declared_media_type=canonical_media_type,
            declared_size_bytes=declared_size_bytes,
            created_by=user_id,
            created_at=now,
            upload_expires_at=upload_expires_at,
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

        return self._build_upload_intent(saved_asset.id, saved_version, max_size_bytes)

    async def create_version_upload_intent(
        self,
        asset_id: UUID,
        user_id: UUID,
        file_name: str,
        declared_media_type: str,
        declared_size_bytes: int,
        idempotency_key: str,
    ) -> UploadIntent:
        asset = await self._authorize_asset(asset_id, user_id)
        if asset.kind != "supporting_document":
            raise AssetReplacementNotAllowed("Only supporting documents accept replacement")

        normalized_name, canonical_media_type, ext = validate_asset_file_and_type(
            asset.kind, file_name, declared_media_type
        )

        kind_limit = ASSET_KIND_LIMITS[asset.kind]
        max_size_bytes = kind_limit["max_size_bytes"]
        assert max_size_bytes is not None

        if declared_size_bytes <= 0:
            raise AssetValidationFailed("Declared size must be positive")
        if declared_size_bytes > max_size_bytes:
            max_mb = max_size_bytes // (1024 * 1024)
            raise AssetSizeLimitExceeded(f"Declared size exceeds {max_mb} MiB limit")

        request_hash = compute_upload_fingerprint(
            asset.kind,
            normalized_name,
            canonical_media_type,
            declared_size_bytes,
        )

        operation = f"version_upload:{asset.id.hex}"
        previous = await self.repository.get_upload_idempotency(
            user_id, asset.project_id, operation, idempotency_key
        )
        if previous is not None:
            if previous.request_hash != request_hash:
                raise AssetIdempotencyConflict(
                    "Idempotency key already used with different parameters"
                )
            existing_version = await self.repository.get_version(previous.version_id)
            if existing_version is not None:
                return self._build_upload_intent(
                    existing_version.asset_id, existing_version, max_size_bytes
                )

        version_id = uuid4()
        now = datetime.now(UTC)
        upload_expires_at = now + timedelta(seconds=self.upload_ttl_seconds)
        project = await self.repository.get_project(asset.project_id)
        team_id = project.team_id if project is not None else uuid4()
        storage_key = (
            f"teams/{team_id}/projects/{asset.project_id}/assets/{asset.id}/{version_id}{ext}"
        )

        version = AssetVersion(
            id=version_id,
            asset_id=asset.id,
            version_number=1,
            state="pending_upload",
            storage_key=storage_key,
            file_name=normalized_name,
            declared_media_type=canonical_media_type,
            declared_size_bytes=declared_size_bytes,
            created_by=user_id,
            created_at=now,
            upload_expires_at=upload_expires_at,
        )
        idempotency = AssetUploadIdempotency(
            user_id=user_id,
            project_id=asset.project_id,
            operation=operation,
            key=idempotency_key,
            request_hash=request_hash,
            asset_id=asset.id,
            version_id=version_id,
        )

        saved_asset, saved_version = await self.repository.save_replacement_version(
            asset.id, version, idempotency
        )

        return self._build_upload_intent(saved_asset.id, saved_version, MAX_DOCUMENT_SIZE_BYTES)

    async def complete_upload(
        self,
        asset_id: UUID,
        version_id: UUID,
        user_id: UUID,
        checksum: str,
        size_bytes: int,
        idempotency_key: str,
    ) -> Asset:
        await self._authorize_asset(asset_id, user_id)

        if not CHECKSUM_REGEX.match(checksum):
            raise AssetValidationFailed("Checksum must be in sha256:<64 lowercase hex> format")
        if size_bytes <= 0:
            raise AssetValidationFailed("Size must be positive")

        (
            locked_asset,
            version,
            current_ver,
        ) = await self.repository.get_asset_and_version_for_completion(asset_id, version_id)
        if locked_asset is None or version is None:
            raise AssetNotFound

        if version.state == "verified":
            if version.checksum == checksum and version.size_bytes == size_bytes:
                return Asset(
                    id=locked_asset.id,
                    project_id=locked_asset.project_id,
                    kind=locked_asset.kind,
                    state=version.state,
                    file_name=version.file_name,
                    current_version_id=version.id,
                    created_by=version.created_by,
                    created_at=version.created_at,
                    media_type=version.media_type,
                    size_bytes=version.size_bytes,
                    checksum=version.checksum,
                    duration_ms=version.duration_ms,
                    retention_expires_at=locked_asset.retention_expires_at,
                    rejection_reason=version.rejection_reason,
                )
            raise AssetCompletionConflict("Checksum or size does not match verified version")

        if version.state in ("deleting", "deleted"):
            raise AssetCompletionConflict("Asset version is being deleted or has been deleted")

        if version.state == "rejected":
            raise AssetCompletionConflict("Asset version has already been rejected")

        if version.state != "pending_upload":
            raise AssetCompletionConflict("Asset version cannot be completed in current state")

        if size_bytes != version.declared_size_bytes:
            await self._reject_version(version, locked_asset, "size_mismatch")
            raise AssetCorrupt("size_mismatch")

        suffix = Path(version.file_name).suffix.lower()
        if not suffix:
            suffix = Path(version.storage_key).suffix.lower()

        kind_limit = ASSET_KIND_LIMITS.get(
            locked_asset.kind, {"max_size_bytes": MAX_DOCUMENT_SIZE_BYTES}
        )
        max_cap = kind_limit["max_size_bytes"]
        assert max_cap is not None

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
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
                    max_cap,
                )
            except (StorageUnavailable, StorageObjectNotFound):
                raise
            except AssetSizeLimitExceeded as err:
                await self._reject_version(version, locked_asset, "file_size_exceeded")
                raise AssetSizeLimitExceeded("file_size_exceeded") from err

            clean_observed = observed_type.split(";")[0].strip().lower()
            clean_declared = version.declared_media_type.split(";")[0].strip().lower()
            if clean_observed != clean_declared:
                await self._reject_version(version, locked_asset, "media_type_mismatch")
                raise AssetCorrupt("media_type_mismatch")

            if observed_bytes != version.declared_size_bytes or observed_bytes != size_bytes:
                await self._reject_version(version, locked_asset, "size_mismatch")
                raise AssetCorrupt("size_mismatch")

            formatted_checksum = f"sha256:{observed_checksum}"
            if formatted_checksum != checksum:
                await self._reject_version(version, locked_asset, "checksum_mismatch")
                raise AssetCorrupt("checksum_mismatch")

            verified_duration_ms: int | None = None
            try:
                if locked_asset.kind == "supporting_document":
                    await self.document_verifier.verify_document(
                        temp_path, version.declared_media_type
                    )
                elif locked_asset.kind in ("presentation_video", "answer_audio"):
                    if self.media_verifier is None:
                        raise StorageUnavailable("Media verifier unavailable")
                    verified_duration_ms = await self.media_verifier.verify_media(
                        temp_path, version.declared_media_type, locked_asset.kind
                    )
                else:
                    raise AssetValidationFailed("Unsupported asset kind")
            except AssetCorrupt as err:
                await self._reject_version(version, locked_asset, err.reason)
                raise

            now = datetime.now(UTC)
            version.state = "verified"
            version.size_bytes = observed_bytes
            version.checksum = formatted_checksum
            version.media_type = version.declared_media_type
            version.duration_ms = verified_duration_ms
            version.completed_at = now

            should_advance = (
                current_ver is None
                or current_ver.state != "verified"
                or version.version_number >= current_ver.version_number
            )

            if should_advance:
                locked_asset.state = "verified"
                locked_asset.file_name = version.file_name
                locked_asset.size_bytes = observed_bytes
                locked_asset.checksum = formatted_checksum
                locked_asset.media_type = version.declared_media_type
                locked_asset.current_version_id = version.id
                locked_asset.duration_ms = verified_duration_ms
                locked_asset.rejection_reason = None
                if (
                    locked_asset.kind in ("presentation_video", "answer_audio")
                    and locked_asset.retention_expires_at is None
                ):
                    base_time = locked_asset.created_at or now
                    locked_asset.retention_expires_at = base_time + timedelta(days=30)

            await self.repository.save_version_completion(version, locked_asset)

            return Asset(
                id=locked_asset.id,
                project_id=locked_asset.project_id,
                kind=locked_asset.kind,
                state=version.state,
                file_name=version.file_name,
                current_version_id=version.id,
                created_by=version.created_by,
                created_at=version.created_at,
                media_type=version.media_type,
                size_bytes=version.size_bytes,
                checksum=version.checksum,
                duration_ms=version.duration_ms,
                retention_expires_at=locked_asset.retention_expires_at,
                rejection_reason=version.rejection_reason,
            )
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
            media_type=asset.media_type or version.declared_media_type,
            size_bytes=asset.size_bytes or 0,
            file_name=asset.file_name,
        )

    async def get_version(self, asset_id: UUID, version_id: UUID, user_id: UUID) -> AssetVersion:
        await self._authorize_asset(asset_id, user_id)
        version = await self.repository.get_version(version_id)
        if version is None or version.asset_id != asset_id:
            raise AssetNotFound
        return version

    async def list_versions(
        self,
        asset_id: UUID,
        user_id: UUID,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[AssetVersion], UUID | None]:
        await self._authorize_asset(asset_id, user_id)
        return await self.repository.list_versions(asset_id, cursor, limit)

    async def create_version_download_intent(
        self, asset_id: UUID, version_id: UUID, user_id: UUID
    ) -> DownloadIntent:
        asset = await self._authorize_asset(asset_id, user_id)
        version = await self.repository.get_version(version_id)
        if version is None or version.asset_id != asset_id:
            raise AssetNotFound
        if version.state != "verified":
            raise AssetNotVerified("Asset version is not in verified state")

        download_url, expires_at = self.storage.generate_download_url(
            version.storage_key, self.download_ttl_seconds
        )

        return DownloadIntent(
            asset_id=asset.id,
            asset_version_id=version.id,
            download_url=download_url,
            expires_at=expires_at,
            media_type=version.media_type or version.declared_media_type,
            size_bytes=version.size_bytes or 0,
            file_name=version.file_name,
        )

    async def cleanup_abandoned_uploads(
        self,
        batch_size: int = 100,
        retention_seconds: int = 86400,
        lease_seconds: int = 300,
        tombstone_delay_seconds: int = 86400,
    ) -> int:
        now = datetime.now(UTC)
        cutoff_created_at = now - timedelta(seconds=retention_seconds)
        cutoff_expires_at = now

        candidates = await self.repository.find_cleanup_candidate_versions(
            cutoff_created_at=cutoff_created_at,
            cutoff_expires_at=cutoff_expires_at,
            now=now,
            limit=batch_size,
        )

        cleaned_count = 0
        for asset_id, version_id in candidates:
            claim_now = datetime.now(UTC)
            claimed = await self.repository.claim_version_for_cleanup(
                asset_id=asset_id,
                version_id=version_id,
                now=claim_now,
                lease_seconds=lease_seconds,
                tombstone_delay_seconds=tombstone_delay_seconds,
                cutoff_created_at=cutoff_created_at,
                cutoff_expires_at=cutoff_expires_at,
            )
            if claimed is None:
                continue

            try:
                await self.storage.delete_object(claimed.storage_key)
            except Exception:
                retry_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
                await self.repository.record_cleanup_failure(
                    asset_id=asset_id,
                    version_id=version_id,
                    next_attempt_at=retry_at,
                )
                logger.warning("Asset cleanup storage deletion failed; retry scheduled")
                continue

            next_sweep_at = datetime.now(UTC) + timedelta(seconds=tombstone_delay_seconds)
            await self.repository.finalize_version_cleanup(
                asset_id=asset_id,
                version_id=version_id,
                next_attempt_at=next_sweep_at,
            )
            cleaned_count += 1

        return cleaned_count
