from datetime import UTC, datetime

from app.domain.asset import Asset, AssetVersion
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_asset(model: AssetModel) -> Asset:
    created_utc = to_utc(model.created_at)
    assert created_utc is not None
    return Asset(
        id=model.id,
        project_id=model.project_id,
        kind=model.kind,
        state=model.state,
        file_name=model.file_name,
        current_version_id=model.current_version_id,
        created_by=model.created_by,
        created_at=created_utc,
        media_type=model.media_type,
        size_bytes=model.size_bytes,
        checksum=model.checksum,
        duration_ms=model.duration_ms,
        retention_expires_at=to_utc(model.retention_expires_at),
        rejection_reason=model.rejection_reason,
    )


def to_version(model: AssetVersionModel) -> AssetVersion:
    created_utc = to_utc(model.created_at)
    assert created_utc is not None
    return AssetVersion(
        id=model.id,
        asset_id=model.asset_id,
        version_number=model.version_number,
        state=model.state,
        storage_key=model.storage_key,
        file_name=model.file_name,
        declared_media_type=model.declared_media_type,
        declared_size_bytes=model.declared_size_bytes,
        created_by=model.created_by,
        created_at=created_utc,
        media_type=model.media_type,
        size_bytes=model.size_bytes,
        checksum=model.checksum,
        duration_ms=model.duration_ms,
        rejection_reason=model.rejection_reason,
        completed_at=to_utc(model.completed_at),
        upload_expires_at=to_utc(model.upload_expires_at),
        cleanup_next_attempt_at=to_utc(model.cleanup_next_attempt_at),
    )
