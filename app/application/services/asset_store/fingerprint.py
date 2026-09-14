import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.domain.asset import (
    AssetUnsupportedMediaType,
    AssetValidationFailed,
)

MAX_DOCUMENT_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_TTL_SECONDS = 900
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 3600
CHECKSUM_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")

SUPPORTED_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

ALLOWED_ASSET_TYPES: dict[str, dict[str, str]] = {
    "supporting_document": {
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
    "presentation_video": {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    },
    "answer_audio": {
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".mp4": "audio/mp4",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
    },
}

ASSET_KIND_LIMITS: dict[str, dict[str, Any]] = {
    "supporting_document": {
        "max_size_bytes": 25 * 1024 * 1024,
        "max_duration_ms": None,
    },
    "presentation_video": {
        "max_size_bytes": 500 * 1024 * 1024,
        "max_duration_ms": 600_000,
    },
    "answer_audio": {
        "max_size_bytes": 25 * 1024 * 1024,
        "max_duration_ms": 120_000,
    },
}


def clamp_ttl(ttl: int) -> int:
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, ttl))


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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


def validate_asset_file_and_type(
    kind: str, file_name: str, declared_media_type: str
) -> tuple[str, str, str]:
    normalized_name = file_name.strip()
    invalid_chars = ("/", "\\", "..")
    if not normalized_name or any(c in normalized_name for c in invalid_chars):
        raise AssetValidationFailed("Invalid file name")

    if kind not in ALLOWED_ASSET_TYPES:
        raise AssetUnsupportedMediaType(f"Unsupported asset kind: {kind}")

    kind_types = ALLOWED_ASSET_TYPES[kind]
    lower_name = normalized_name.lower()
    ext: str | None = None
    for allowed_ext in kind_types:
        if lower_name.endswith(allowed_ext):
            ext = allowed_ext
            break

    if ext is None:
        if kind == "supporting_document":
            raise AssetUnsupportedMediaType("File extension must be .pdf or .pptx")
        raise AssetUnsupportedMediaType(f"File extension not supported for kind {kind}")

    canonical_media_type = kind_types[ext]
    normalized_media_type = declared_media_type.split(";")[0].strip().lower()
    if normalized_media_type != canonical_media_type:
        raise AssetUnsupportedMediaType(f"Declared media type must be {canonical_media_type}")

    return normalized_name, canonical_media_type, ext


def validate_document_file_and_type(
    file_name: str, declared_media_type: str
) -> tuple[str, str, str]:
    return validate_asset_file_and_type("supporting_document", file_name, declared_media_type)
