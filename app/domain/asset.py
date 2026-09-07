from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class AssetDomainError(Exception):
    pass


class AssetNotFound(AssetDomainError):
    pass


class AssetUnsupportedMediaType(AssetDomainError):
    pass


class AssetSizeLimitExceeded(AssetDomainError):
    pass


class AssetValidationFailed(AssetDomainError):
    def __init__(self, reason: str = "validation_failed"):
        super().__init__(reason)
        self.reason = reason


class AssetCorrupt(AssetValidationFailed):
    pass


class AssetConflict(AssetDomainError):
    pass


class AssetIdempotencyConflict(AssetConflict):
    pass


class AssetCompletionConflict(AssetConflict):
    pass


class AssetNotVerified(AssetConflict):
    pass


class AssetReplacementNotAllowed(AssetConflict):
    pass


class StorageUnavailable(AssetDomainError):
    pass


class StorageObjectNotFound(AssetValidationFailed):
    def __init__(self, reason: str = "object_not_found_in_storage"):
        super().__init__(reason)


@dataclass
class Asset:
    id: UUID
    project_id: UUID
    kind: str
    state: str
    file_name: str
    current_version_id: UUID | None
    created_by: UUID
    created_at: datetime
    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    duration_ms: int | None = None
    retention_expires_at: datetime | None = None
    rejection_reason: str | None = None


@dataclass
class AssetVersion:
    id: UUID
    asset_id: UUID
    version_number: int
    state: str
    storage_key: str
    file_name: str
    declared_media_type: str
    declared_size_bytes: int
    created_by: UUID
    created_at: datetime
    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    duration_ms: int | None = None
    rejection_reason: str | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UploadIntent:
    asset_id: UUID
    asset_version_id: UUID
    upload_url: str
    method: str
    required_headers: dict[str, str]
    expires_at: datetime
    maximum_size_bytes: int


@dataclass(frozen=True, slots=True)
class DownloadIntent:
    asset_id: UUID
    asset_version_id: UUID
    download_url: str
    expires_at: datetime
    media_type: str
    size_bytes: int
    file_name: str
