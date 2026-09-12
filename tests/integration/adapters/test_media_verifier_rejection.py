import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.asset import AssetCorrupt
from app.infrastructure.media.ffmpeg_verifier import FFmpegMediaVerifier
from tests.integration.adapters.conftest import FFMPEG_AVAILABLE

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")


@pytest.mark.anyio
async def test_renamed_matroska_rejected(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    mkv_path = media_fixtures["matroska_mkv"]
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(mkv_path, "video/webm", "presentation_video")
    assert exc_info.value.reason == "container_signature_mismatch"

    with pytest.raises(AssetCorrupt) as exc_info2:
        await verifier.verify_media(mkv_path, "audio/webm", "answer_audio")
    assert exc_info2.value.reason == "container_signature_mismatch"


@pytest.mark.anyio
async def test_unrelated_quicktime_and_3gp_rejected(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    mov_path = media_fixtures["quicktime_mov"]
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(mov_path, "video/mp4", "presentation_video")
    assert exc_info.value.reason == "container_signature_mismatch"

    t3gp_path = media_fixtures["mobile_3gp"]
    with pytest.raises(AssetCorrupt) as exc_info2:
        await verifier.verify_media(t3gp_path, "video/mp4", "presentation_video")
    assert exc_info2.value.reason == "container_signature_mismatch"

    with pytest.raises(AssetCorrupt) as exc_info3:
        await verifier.verify_media(t3gp_path, "audio/mp4", "answer_audio")
    assert exc_info3.value.reason == "container_signature_mismatch"


@pytest.mark.anyio
async def test_corrupt_and_truncated_files(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
    tmp_path: Path,
) -> None:
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    with pytest.raises(AssetCorrupt) as exc_empty:
        await verifier.verify_media(empty_file, "video/mp4", "presentation_video")
    assert exc_empty.value.reason == "empty_media"

    missing_file = tmp_path / "nonexistent.mp4"
    with pytest.raises(AssetCorrupt) as exc_missing:
        await verifier.verify_media(missing_file, "video/mp4", "presentation_video")
    assert exc_missing.value.reason == "corrupt_media"

    garbage_file = tmp_path / "garbage.mp4"
    garbage_file.write_bytes(b"0123456789abcdef" * 16)
    with pytest.raises(AssetCorrupt) as exc_garbage:
        await verifier.verify_media(garbage_file, "video/mp4", "presentation_video")
    assert exc_garbage.value.reason == "container_signature_mismatch"

    # Truncated faststart MP4
    faststart_src = media_fixtures["faststart_mp4"]
    data = faststart_src.read_bytes()
    truncated_file = tmp_path / "truncated.mp4"
    truncated_file.write_bytes(data[: len(data) // 2])
    with pytest.raises(AssetCorrupt) as exc_trunc:
        await verifier.verify_media(truncated_file, "video/mp4", "presentation_video")
    assert exc_trunc.value.reason == "corrupt_media"


@pytest.mark.anyio
async def test_wrong_stream_types(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    # Audio passed as presentation_video -> missing video stream
    audio_file = media_fixtures["audio_mp4"]
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(audio_file, "video/mp4", "presentation_video")
    assert exc_info.value.reason == "missing_video_stream"

    # Video passed as answer_audio -> unexpected video stream
    video_file = media_fixtures["video_mp4_av"]
    with pytest.raises(AssetCorrupt) as exc_info2:
        await verifier.verify_media(video_file, "audio/mp4", "answer_audio")
    assert exc_info2.value.reason == "unexpected_video_stream"


@pytest.mark.anyio
async def test_unsupported_kinds_and_types(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    with pytest.raises(AssetCorrupt) as exc_kind:
        await verifier.verify_media(path, "video/mp4", "supporting_document")
    assert exc_kind.value.reason == "unsupported_media_kind"

    with pytest.raises(AssetCorrupt) as exc_type:
        await verifier.verify_media(path, "video/avi", "presentation_video")
    assert exc_type.value.reason == "unsupported_media_type"


@pytest.mark.anyio
async def test_secondary_stream_unsupported_codec(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["unsupported_secondary"]
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(path, "audio/mp4", "answer_audio")
    assert exc_info.value.reason == "unsupported_audio_codec"


@pytest.mark.anyio
async def test_unsupported_stream_type_subtitle(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["with_sub"]
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(path, "video/mp4", "presentation_video")
    assert exc_info.value.reason == "unsupported_stream_type"


@pytest.mark.anyio
async def test_truncated_webm(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
    tmp_path: Path,
) -> None:
    src = media_fixtures["video_webm"]
    data = src.read_bytes()
    trunc_webm = tmp_path / "trunc.webm"
    trunc_webm.write_bytes(data[: len(data) // 2])
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_media(trunc_webm, "video/webm", "presentation_video")
    assert exc_info.value.reason == "corrupt_media"


@pytest.mark.anyio
async def test_header_decoded_duration_mismatch(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    # Header claims 10 seconds, but 1s file decoded
    mock_payload = {
        "format": {"format_name": "mov,mp4", "duration": "10.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264"}],
    }
    with patch.object(
        verifier,
        "_run_bounded_process",
        return_value=(0, json.dumps(mock_payload).encode("utf-8"), b""),
    ):
        with pytest.raises(AssetCorrupt) as exc_info:
            await verifier.verify_media(path, "video/mp4", "presentation_video")
        assert exc_info.value.reason == "duration_mismatch"


@pytest.mark.anyio
async def test_invalid_header_durations(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    for bad_dur in ["-5.0", "nan", "inf"]:
        mock_payload = {
            "format": {"format_name": "mov,mp4", "duration": bad_dur},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
        with patch.object(
            verifier,
            "_run_bounded_process",
            return_value=(0, json.dumps(mock_payload).encode("utf-8"), b""),
        ):
            with pytest.raises(AssetCorrupt) as exc_info:
                await verifier.verify_media(path, "video/mp4", "presentation_video")
            assert exc_info.value.reason == "invalid_duration"
