import shutil
import subprocess
from pathlib import Path

import pytest

from app.infrastructure.media.ffmpeg_verifier import FFmpegMediaVerifier

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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
