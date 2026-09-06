from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UploadIntentRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    file_name: str = Field(min_length=1, max_length=255)
    declared_media_type: str = Field(min_length=1, max_length=100)
    declared_size_bytes: int = Field(gt=0)


class UploadIntentResponse(BaseModel):
    asset_id: UUID
    asset_version_id: UUID
    upload_url: str
    method: str = "PUT"
    required_headers: dict[str, str]
    expires_at: datetime
    maximum_size_bytes: int


class CompleteUploadRequest(BaseModel):
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class AssetResponse(BaseModel):
    id: UUID
    version_id: UUID | None = None
    project_id: UUID
    kind: str
    state: str
    file_name: str
    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    duration_ms: int | None = None
    created_by: UUID
    created_at: datetime
    retention_expires_at: datetime | None = None
    rejection_reason: str | None = None


class AssetPage(BaseModel):
    items: list[AssetResponse]
    next_cursor: str | None = None


class DownloadIntentResponse(BaseModel):
    asset_id: UUID
    asset_version_id: UUID
    download_url: str
    expires_at: datetime
    media_type: str
    size_bytes: int
    file_name: str
