import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.asset import AssetCorrupt, StorageUnavailable
from app.infrastructure.media.ffmpeg_verifier import FFmpegMediaVerifier
from app.infrastructure.media.worker_launcher import main as launcher_main
from tests.integration.adapters.conftest import FFMPEG_AVAILABLE

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")


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


def test_worker_launcher_direct() -> None:
    # Missing tool exits with 127
    with patch.object(sys, "argv", ["worker_launcher.py", "--", "/nonexistent/tool"]):
        with pytest.raises(SystemExit) as exc_info:
            launcher_main()
        assert exc_info.value.code == 127


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
