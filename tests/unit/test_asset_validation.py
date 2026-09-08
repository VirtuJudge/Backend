from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.application.services.asset_store.fingerprint import (
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    clamp_ttl,
    compute_upload_fingerprint,
    to_utc,
    validate_asset_file_and_type,
)
from app.domain.asset import AssetUnsupportedMediaType, AssetValidationFailed


def test_compute_upload_fingerprint_deterministic() -> None:
    fp1 = compute_upload_fingerprint("supporting_document", "doc.pdf", "application/pdf", 1024)
    fp2 = compute_upload_fingerprint("supporting_document", "doc.pdf", "application/pdf", 1024)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Sensitive to size
    fp3 = compute_upload_fingerprint("supporting_document", "doc.pdf", "application/pdf", 1025)
    assert fp1 != fp3

    # Sensitive to kind
    fp4 = compute_upload_fingerprint("answer_audio", "doc.pdf", "application/pdf", 1024)
    assert fp1 != fp4

    # Normalizes media type case
    fp5 = compute_upload_fingerprint("supporting_document", "doc.pdf", "APPLICATION/PDF", 1024)
    assert fp1 == fp5


def test_clamp_ttl() -> None:
    assert clamp_ttl(0) == MIN_TTL_SECONDS
    assert clamp_ttl(59) == MIN_TTL_SECONDS
    assert clamp_ttl(60) == 60
    assert clamp_ttl(900) == 900
    assert clamp_ttl(3600) == 3600
    assert clamp_ttl(3601) == MAX_TTL_SECONDS
    assert clamp_ttl(100000) == MAX_TTL_SECONDS


def test_to_utc() -> None:
    assert to_utc(None) is None
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = to_utc(naive)
    assert aware is not None
    assert aware.tzinfo == UTC

    # Non-UTC timezone
    plus_two = timezone(timedelta(hours=2))
    tz_aware = datetime(2026, 1, 1, 14, 0, 0, tzinfo=plus_two)
    utc_converted = to_utc(tz_aware)
    assert utc_converted is not None
    assert utc_converted.hour == 12
    assert utc_converted.tzinfo == UTC


def test_validate_asset_file_and_type_valid() -> None:
    name, mime, ext = validate_asset_file_and_type(
        "supporting_document", "report.pdf", "application/pdf"
    )
    assert name == "report.pdf"
    assert mime == "application/pdf"
    assert ext == ".pdf"

    name, mime, ext = validate_asset_file_and_type("presentation_video", "video.mp4", "video/mp4")
    assert name == "video.mp4"
    assert mime == "video/mp4"
    assert ext == ".mp4"


def test_validate_asset_file_and_type_invalid_names() -> None:
    for bad_name in ["", "   ", "../escape.pdf", "foo/bar.pdf", "foo\\bar.pdf"]:
        with pytest.raises(AssetValidationFailed):
            validate_asset_file_and_type("supporting_document", bad_name, "application/pdf")


def test_validate_asset_file_and_type_unsupported_kind() -> None:
    with pytest.raises(AssetUnsupportedMediaType):
        validate_asset_file_and_type("unknown_kind", "doc.pdf", "application/pdf")


def test_validate_asset_file_and_type_extension_mime_mismatch() -> None:
    with pytest.raises(AssetUnsupportedMediaType):
        validate_asset_file_and_type("supporting_document", "doc.pptx", "application/pdf")

    with pytest.raises(AssetUnsupportedMediaType):
        validate_asset_file_and_type("supporting_document", "doc.pdf", "image/png")
