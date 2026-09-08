import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.asset import AssetCorrupt, StorageUnavailable
from app.infrastructure.media.ffmpegVerifier import (
    MEDIA_RULES,
    FFmpegMediaVerifier,
    _extract_ebml_doctype,
    _validate_mp4_brands,
)
from app.infrastructure.media.worker_launcher import main as launcher_main

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")


@pytest.fixture(scope="module")
def media_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out_dir = tmp_path_factory.mktemp("media_fixtures")
    paths: dict[str, Path] = {}

    def _run(cmd: list[str]) -> None:
        subprocess.run(cmd, check=True, capture_output=True)

    # 1. Video MP4 (h264)
    v_mp4 = out_dir / "test_video.mp4"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-y",
            str(v_mp4),
        ]
    )
    paths["video_mp4"] = v_mp4

    # 2. Video WebM (vp8)
    v_webm = out_dir / "test_video.webm"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-c:v",
            "libvpx",
            "-y",
            str(v_webm),
        ]
    )
    paths["video_webm"] = v_webm

    # 3. Audio WebM (opus)
    a_webm = out_dir / "test_audio.webm"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:a",
            "libopus",
            "-y",
            str(a_webm),
        ]
    )
    paths["audio_webm"] = a_webm

    # 4. Audio Ogg (vorbis)
    a_ogg = out_dir / "test_audio.ogg"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:a",
            "libvorbis",
            "-y",
            str(a_ogg),
        ]
    )
    paths["audio_ogg"] = a_ogg

    # 5. Audio MP4 / M4A (aac)
    a_mp4 = out_dir / "test_audio.m4a"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:a",
            "aac",
            "-y",
            str(a_mp4),
        ]
    )
    paths["audio_mp4"] = a_mp4

    # 6. Audio WAV (pcm_s16le)
    a_wav = out_dir / "test_audio.wav"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(a_wav),
        ]
    )
    paths["audio_wav"] = a_wav

    # Video MP4 with audio
    v_mp4_av = out_dir / "test_av.mp4"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            str(v_mp4_av),
        ]
    )
    paths["video_mp4_av"] = v_mp4_av

    # Browser WebM without header duration
    b_webm = out_dir / "browser_live.webm"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-c:v",
            "libvpx",
            "-f",
            "webm",
            "-dash",
            "0",
            "-live",
            "1",
            "-y",
            str(b_webm),
        ]
    )
    paths["browser_webm"] = b_webm

    # Browser Audio WebM without header duration
    b_awebm = out_dir / "browser_audio_live.webm"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "-dash",
            "0",
            "-live",
            "1",
            "-y",
            str(b_awebm),
        ]
    )
    paths["browser_audio_webm"] = b_awebm

    # Matroska MKV file (DocType matroska)
    m_mkv = out_dir / "renamed_matroska.mkv"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-y",
            str(m_mkv),
        ]
    )
    paths["matroska_mkv"] = m_mkv

    # QuickTime MOV (brand qt)
    q_mov = out_dir / "quicktime.mov"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-f",
            "mov",
            "-y",
            str(q_mov),
        ]
    )
    paths["quicktime_mov"] = q_mov

    # 3GP file (brand 3gp4)
    t_3gp = out_dir / "mobile.3gp"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=176x144:rate=10",
            "-c:v",
            "h263",
            "-y",
            str(t_3gp),
        ]
    )
    paths["mobile_3gp"] = t_3gp

    # Faststart MP4 (for truncation testing)
    f_mp4 = out_dir / "faststart.mp4"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-movflags",
            "faststart",
            "-y",
            str(f_mp4),
        ]
    )
    paths["faststart_mp4"] = f_mp4

    # MP4 with secondary unsupported audio codec (mp2)
    bad_sec = out_dir / "unsupported_secondary.mp4"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=440",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:frequency=880",
            "-map",
            "0:a",
            "-map",
            "1:a",
            "-c:a:0",
            "aac",
            "-c:a:1",
            "ac3",
            "-y",
            str(bad_sec),
        ]
    )
    paths["unsupported_secondary"] = bad_sec

    # MP4 with subtitle stream
    sub_srt = out_dir / "temp.srt"
    sub_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nSub\n")
    sub_mp4 = out_dir / "with_sub.mp4"
    _run(
        [
            "ffmpeg",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-i",
            str(sub_srt),
            "-c:v",
            "libx264",
            "-c:s",
            "mov_text",
            "-y",
            str(sub_mp4),
        ]
    )
    paths["with_sub"] = sub_mp4

    return paths


@pytest.fixture
def verifier() -> FFmpegMediaVerifier:
    return FFmpegMediaVerifier(timeout_seconds=15.0)


def test_default_verification_budget_exceeds_maximum_media_duration() -> None:
    default_verifier = FFmpegMediaVerifier()
    maximum_duration_seconds = max(rule["max_duration_ms"] for rule in MEDIA_RULES.values()) / 1000

    assert default_verifier.timeout_seconds > maximum_duration_seconds
    assert default_verifier.max_cpu_seconds > maximum_duration_seconds


def test_installed_tools_and_flags_supported() -> None:
    assert shutil.which("ffmpeg") is not None
    assert shutil.which("ffprobe") is not None

    res_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-f",
            "mov,mp4,m4a,3gp,3g2,mj2",
            "-use_absolute_path",
            "0",
            "-enable_drefs",
            "0",
            "-h",
            "demuxer=mov,mp4,m4a,3gp,3g2,mj2",
        ],
        capture_output=True,
    )
    assert res_probe.returncode == 0

    res_decode = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-xerror",
            "-err_detect",
            "explode",
            "-protocol_whitelist",
            "file",
            "-f",
            "mov,mp4,m4a,3gp,3g2,mj2",
            "-use_absolute_path",
            "0",
            "-enable_drefs",
            "0",
            "-h",
            "demuxer=mov,mp4,m4a,3gp,3g2,mj2",
        ],
        capture_output=True,
    )
    assert res_decode.returncode == 0


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
async def test_missing_tools(
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    bad_verifier = FFmpegMediaVerifier(ffprobe_path="/nonexistent/ffprobe")
    with pytest.raises(StorageUnavailable) as exc_info:
        await bad_verifier.verify_media(path, "video/mp4", "presentation_video")
    assert "unavailable" in str(exc_info.value).lower()

    bad_verifier2 = FFmpegMediaVerifier(ffmpeg_path="/nonexistent/ffmpeg")
    with pytest.raises(StorageUnavailable) as exc_info2:
        await bad_verifier2.verify_media(path, "video/mp4", "presentation_video")
    assert "unavailable" in str(exc_info2.value).lower()


@pytest.mark.anyio
async def test_timeout_and_cancellation(
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    timeout_verifier = FFmpegMediaVerifier(timeout_seconds=0.00001)
    with pytest.raises(StorageUnavailable) as exc_info:
        await timeout_verifier.verify_media(path, "video/mp4", "presentation_video")
    assert "timed out" in str(exc_info.value).lower()

    normal_verifier = FFmpegMediaVerifier(timeout_seconds=30.0)
    task = asyncio.create_task(
        normal_verifier.verify_media(path, "video/mp4", "presentation_video")
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_ebml_and_brand_helpers() -> None:
    assert _extract_ebml_doctype(b"short") is None
    assert _extract_ebml_doctype(b"\x1a\x45\xdf\xa3\x9f\x42\x82\x84webm") == "webm"
    assert _extract_ebml_doctype(b"\x1a\x45\xdf\xa3\xa3\x42\x82\x88matroska") == "matroska"

    assert not _validate_mp4_brands(b"short", "video/mp4")
    # QuickTime brand rejection
    qt_box = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  "
    assert not _validate_mp4_brands(qt_box, "video/mp4")
    # 3GP brand rejection
    t3gp_box = b"\x00\x00\x00\x1cftyp3gp4\x00\x00\x02\x003gp4isom"
    assert not _validate_mp4_brands(t3gp_box, "video/mp4")
    # Valid MP4 isom box
    isom_box = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert _validate_mp4_brands(isom_box, "video/mp4")


def test_worker_launcher_direct() -> None:
    # Missing tool exits with 127
    with patch.object(sys, "argv", ["worker_launcher.py", "--", "/nonexistent/tool"]):
        with pytest.raises(SystemExit) as exc_info:
            launcher_main()
        assert exc_info.value.code == 127


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


@pytest.mark.anyio
async def test_resource_limit_signals(
    verifier: FFmpegMediaVerifier,
    media_fixtures: dict[str, Path],
) -> None:
    path = media_fixtures["video_mp4"]
    for sig_code in [-24, 152, -25, 153]:
        with patch.object(
            verifier,
            "_run_bounded_process",
            return_value=(sig_code, b"", b""),
        ):
            with pytest.raises(AssetCorrupt) as exc_info:
                await verifier.verify_media(path, "video/mp4", "presentation_video")
            assert exc_info.value.reason == "resource_limit_exceeded"
