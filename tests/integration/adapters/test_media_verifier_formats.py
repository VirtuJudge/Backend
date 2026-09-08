import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.asset import AssetCorrupt
from app.infrastructure.media.container import MEDIA_RULES
from app.infrastructure.media.ffmpeg_verifier import FFmpegMediaVerifier
from tests.integration.adapters.conftest import FFMPEG_AVAILABLE

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")


def test_default_verification_budget_exceeds_maximum_media_duration() -> None:
    default_verifier = FFmpegMediaVerifier()
    maximum_duration_seconds = max(rule["max_duration_ms"] for rule in MEDIA_RULES.values()) / 1000

    assert default_verifier.timeout_seconds > maximum_duration_seconds
    assert default_verifier.max_cpu_seconds > maximum_duration_seconds


@pytest.mark.parametrize(
    ("fixture_key", "media_type", "kind"),
    [
        ("video_mp4", "video/mp4", "presentation_video"),
        ("video_webm", "video/webm", "presentation_video"),
        ("audio_webm", "audio/webm", "answer_audio"),
        ("audio_ogg", "audio/ogg", "answer_audio"),
        ("audio_mp4", "audio/mp4", "answer_audio"),
        ("audio_wav", "audio/wav", "answer_audio"),
    ],
)
@pytest.mark.anyio
async def test_all_six_mime_pairs(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
    fixture_key: str,
    media_type: str,
    kind: str,
) -> None:
    path = media_fixtures[fixture_key]
    dur = await verifier.verify_media(path, media_type, kind)
    assert isinstance(dur, int)
    assert 800 <= dur <= 1200


@pytest.mark.anyio
async def test_presentation_video_with_audio(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4_av"]
    dur = await verifier.verify_media(path, "video/mp4", "presentation_video")
    assert isinstance(dur, int)
    assert 800 <= dur <= 1200


@pytest.mark.anyio
async def test_browser_webm_missing_header_duration(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    v_path = media_fixtures["browser_webm"]
    v_dur = await verifier.verify_media(v_path, "video/webm", "presentation_video")
    assert isinstance(v_dur, int)
    assert 800 <= v_dur <= 1200

    a_path = media_fixtures["browser_audio_webm"]
    a_dur = await verifier.verify_media(a_path, "audio/webm", "answer_audio")
    assert isinstance(a_dur, int)
    assert 800 <= a_dur <= 1200


@pytest.mark.anyio
async def test_duration_boundary_and_overlong(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    # Mock MEDIA_RULES max_duration_ms to 500ms so 1000ms triggers overlong_media
    with patch.dict(MEDIA_RULES["presentation_video"], {"max_duration_ms": 500}):
        with pytest.raises(AssetCorrupt) as exc_info:
            await verifier.verify_media(path, "video/mp4", "presentation_video")
        assert exc_info.value.reason == "overlong_media"


@pytest.mark.anyio
async def test_bounded_stream_count(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    mock_streams = [{"codec_type": "video", "codec_name": "h264"} for _ in range(20)]
    mock_payload = {
        "format": {"format_name": "mov,mp4", "duration": "1.0"},
        "streams": mock_streams,
    }
    with patch.object(
        verifier,
        "_run_bounded_process",
        return_value=(0, json.dumps(mock_payload).encode("utf-8"), b""),
    ):
        with pytest.raises(AssetCorrupt) as exc_info:
            await verifier.verify_media(path, "video/mp4", "presentation_video")
        assert exc_info.value.reason == "corrupt_media"
