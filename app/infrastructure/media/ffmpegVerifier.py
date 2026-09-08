import asyncio
import contextlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

from app.application.interfaces.mediaVerifier import MediaVerifierPort
from app.domain.asset import AssetCorrupt, StorageUnavailable

LAUNCHER_PATH = Path(__file__).parent / "worker_launcher.py"


def _extract_ebml_doctype(data: bytes) -> str | None:
    if len(data) < 4 or data[:4] != b"\x1a\x45\xdf\xa3":
        return None
    header_end = data.find(b"\x18\x53\x80\x67")
    limit = header_end if header_end != -1 else min(len(data), 4096)
    doctype_pos = data.find(b"\x42\x82", 4, limit)
    if doctype_pos == -1:
        return None
    pos = doctype_pos + 2
    if pos >= limit:
        return None
    first_byte = data[pos]
    if first_byte == 0:
        return None
    mask = 0x80
    vint_len = 1
    while mask and not (first_byte & mask):
        mask >>= 1
        vint_len += 1
    if pos + vint_len > limit:
        return None
    val = first_byte & (mask - 1)
    for i in range(1, vint_len):
        val = (val << 8) | data[pos + i]
    str_start = pos + vint_len
    str_end = str_start + val
    if str_end > len(data):
        return None
    return data[str_start:str_end].rstrip(b"\x00").decode("ascii", errors="ignore").lower()


def _validate_mp4_brands(data: bytes, media_type: str) -> bool:
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(data[0:4], "big")
    box_size = min(box_size, len(data))
    major_brand = data[8:12].decode("latin1", errors="ignore")
    major_lower = major_brand.lower().strip()
    if major_lower.startswith(("3gp", "3g2", "3ge", "3gg")) or major_lower == "qt":
        return False
    brands = {major_brand.strip()}
    for i in range(16, box_size - 3, 4):
        brand = data[i : i + 4].decode("latin1", errors="ignore").strip()
        if brand:
            brands.add(brand)
    brands_lower = {b.lower() for b in brands}
    if media_type == "video/mp4":
        allowed = {"mp41", "mp42", "isom", "iso2", "iso3", "iso4", "iso5", "iso6", "avc1", "dash"}
        return bool(brands_lower & allowed)
    if media_type == "audio/mp4":
        allowed = {
            "m4a",
            "m4b",
            "mp41",
            "mp42",
            "isom",
            "iso2",
            "iso3",
            "iso4",
            "iso5",
            "iso6",
            "dash",
        }
        return bool(brands_lower & allowed)
    return False


MEDIA_RULES: dict[str, dict[str, Any]] = {
    "presentation_video": {
        "max_duration_ms": 600_000,
        "media_types": {
            "video/mp4": {
                "demuxer": "mov,mp4,m4a,3gp,3g2,mj2",
                "containers": {"mov,mp4,m4a,3gp,3g2,mj2", "mp4", "mov", "m4a"},
                "video_codecs": {"h264", "hevc", "av1", "vp9"},
                "audio_codecs": {"aac", "mp3", "opus", "flac"},
                "signature_check": lambda data: _validate_mp4_brands(data, "video/mp4"),
            },
            "video/webm": {
                "demuxer": "matroska,webm",
                "containers": {"matroska,webm", "webm", "matroska"},
                "video_codecs": {"vp8", "vp9", "av1"},
                "audio_codecs": {"opus", "vorbis"},
                "signature_check": lambda data: _extract_ebml_doctype(data) == "webm",
            },
        },
    },
    "answer_audio": {
        "max_duration_ms": 120_000,
        "media_types": {
            "audio/webm": {
                "demuxer": "matroska,webm",
                "containers": {"matroska,webm", "webm", "matroska"},
                "video_codecs": set(),
                "audio_codecs": {"opus", "vorbis"},
                "signature_check": lambda data: _extract_ebml_doctype(data) == "webm",
            },
            "audio/ogg": {
                "demuxer": "ogg",
                "containers": {"ogg"},
                "video_codecs": set(),
                "audio_codecs": {"opus", "vorbis", "flac"},
                "signature_check": lambda data: len(data) >= 4 and data[:4] == b"OggS",
            },
            "audio/mp4": {
                "demuxer": "mov,mp4,m4a,3gp,3g2,mj2",
                "containers": {"mov,mp4,m4a,3gp,3g2,mj2", "mp4", "mov", "m4a"},
                "video_codecs": set(),
                "audio_codecs": {"aac", "mp3", "opus", "flac", "alac"},
                "signature_check": lambda data: _validate_mp4_brands(data, "audio/mp4"),
            },
            "audio/wav": {
                "demuxer": "wav",
                "containers": {"wav"},
                "video_codecs": set(),
                "audio_codecs": {
                    "pcm_s16le",
                    "pcm_s24le",
                    "pcm_s32le",
                    "pcm_u8",
                    "pcm_f32le",
                    "pcm_alaw",
                    "pcm_mulaw",
                },
                "signature_check": lambda data: (
                    len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
                ),
            },
        },
    },
}


class FFmpegMediaVerifier(MediaVerifierPort):
    def __init__(
        self,
        timeout_seconds: float = 900.0,
        ffprobe_path: str = "ffprobe",
        ffmpeg_path: str = "ffmpeg",
        max_memory_mb: int = 1024,
        max_cpu_seconds: int = 660,
        max_fsize_mb: int = 10,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_path = ffmpeg_path
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds
        self.max_fsize_mb = max_fsize_mb

    def _build_launcher_cmd(self, tool_cmd: list[str]) -> list[str]:
        return [
            sys.executable,
            str(LAUNCHER_PATH.resolve()),
            "--as-mb",
            str(self.max_memory_mb),
            "--cpu-s",
            str(self.max_cpu_seconds),
            "--fsize-mb",
            str(self.max_fsize_mb),
            "--",
            *tool_cmd,
        ]

    async def _run_bounded_process(
        self,
        cmd: list[str],
        max_stdout: int = 65536,
        max_stderr: int = 65536,
    ) -> tuple[int, bytes, bytes]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as err:
            raise StorageUnavailable("Media verification tool unavailable") from err
        except OSError as err:
            raise StorageUnavailable("Failed to execute media verification worker") from err

        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout_reader = proc.stdout
        stderr_reader = proc.stderr

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_len = 0
        stderr_len = 0
        truncated = False

        async def read_out() -> None:
            nonlocal stdout_len, truncated
            while True:
                chunk = await stdout_reader.read(4096)
                if not chunk:
                    break
                if stdout_len < max_stdout:
                    keep = chunk[: max_stdout - stdout_len]
                    stdout_chunks.append(keep)
                    stdout_len += len(keep)
                if stdout_len >= max_stdout:
                    truncated = True
                    with contextlib.suppress(Exception):
                        proc.kill()
                    break

        async def read_err() -> None:
            nonlocal stderr_len
            while True:
                chunk = await stderr_reader.read(4096)
                if not chunk:
                    break
                if stderr_len < max_stderr:
                    keep = chunk[: max_stderr - stderr_len]
                    stderr_chunks.append(keep)
                stderr_len += len(chunk)
                if stderr_len >= max_stderr * 16:
                    with contextlib.suppress(Exception):
                        proc.kill()
                    break

        out_task = asyncio.create_task(read_out())
        err_task = asyncio.create_task(read_err())

        try:
            await asyncio.wait_for(
                asyncio.gather(out_task, err_task, proc.wait()),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as err:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise StorageUnavailable("Media verification service timed out") from err
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise
        finally:
            for t in (out_task, err_task):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(out_task, err_task, return_exceptions=True)
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        if truncated:
            raise AssetCorrupt("corrupt_media")

        return (
            proc.returncode if proc.returncode is not None else 1,
            b"".join(stdout_chunks),
            b"".join(stderr_chunks),
        )

    async def verify_media(self, file_path: Path, media_type: str, kind: str) -> int:
        kind_rule = MEDIA_RULES.get(kind)
        if kind_rule is None:
            raise AssetCorrupt("unsupported_media_kind")

        type_rule = kind_rule["media_types"].get(media_type)
        if type_rule is None:
            raise AssetCorrupt("unsupported_media_type")

        if not file_path.is_file():
            raise AssetCorrupt("corrupt_media")

        try:
            if file_path.stat().st_size == 0:
                raise AssetCorrupt("empty_media")
            with file_path.open("rb") as f:
                header = f.read(4096)
        except OSError:
            raise AssetCorrupt("corrupt_media") from None

        if not header:
            raise AssetCorrupt("empty_media")

        if not type_rule["signature_check"](header):
            raise AssetCorrupt("container_signature_mismatch")

        if not shutil.which(self.ffprobe_path) or not shutil.which(self.ffmpeg_path):
            raise StorageUnavailable("Media verification tool unavailable")

        ffprobe_cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-f",
            type_rule["demuxer"],
        ]
        if media_type in ("video/mp4", "audio/mp4"):
            ffprobe_cmd.extend(["-use_absolute_path", "0", "-enable_drefs", "0"])
        ffprobe_cmd.extend(
            [
                "-show_entries",
                "format=format_name,duration,nb_streams",
                "-show_entries",
                "stream=index,codec_name,codec_type",
                "-of",
                "json",
                str(file_path.resolve()),
            ]
        )

        probe_launcher_cmd = self._build_launcher_cmd(ffprobe_cmd)
        probe_rc, probe_stdout, _ = await self._run_bounded_process(probe_launcher_cmd)

        if probe_rc in (-24, 152, -25, 153, -9, 137):
            raise AssetCorrupt("resource_limit_exceeded")
        if probe_rc in (126, 127):
            raise StorageUnavailable("Media verification tool unavailable")
        if probe_rc != 0:
            raise AssetCorrupt("corrupt_media")

        try:
            probe_data = json.loads(probe_stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AssetCorrupt("corrupt_media") from None

        format_info = probe_data.get("format", {})
        format_name = format_info.get("format_name", "")
        split_formats = {fmt.strip() for fmt in format_name.split(",")}
        if not (split_formats & type_rule["containers"]):
            raise AssetCorrupt("container_mismatch")

        streams = probe_data.get("streams", [])
        if not streams:
            raise AssetCorrupt("empty_media")
        if len(streams) > 16:
            raise AssetCorrupt("corrupt_media")

        video_count = 0
        audio_count = 0
        allowed_video = type_rule.get("video_codecs", set())
        allowed_audio = type_rule.get("audio_codecs", set())

        for stream in streams:
            stype = stream.get("codec_type")
            sname = stream.get("codec_name")
            if not sname:
                raise AssetCorrupt("corrupt_media")
            if stype == "video":
                if kind == "answer_audio":
                    raise AssetCorrupt("unexpected_video_stream")
                if sname not in allowed_video:
                    raise AssetCorrupt("unsupported_video_codec")
                video_count += 1
            elif stype == "audio":
                if sname not in allowed_audio:
                    raise AssetCorrupt("unsupported_audio_codec")
                audio_count += 1
            else:
                raise AssetCorrupt("unsupported_stream_type")

        if kind == "presentation_video" and video_count < 1:
            raise AssetCorrupt("missing_video_stream")
        if kind == "answer_audio":
            if video_count > 0:
                raise AssetCorrupt("unexpected_video_stream")
            if audio_count < 1:
                raise AssetCorrupt("missing_audio_stream")

        header_duration_ms: int | None = None
        raw_dur = format_info.get("duration")
        if raw_dur is not None:
            try:
                val_float = float(raw_dur)
                if math.isnan(val_float) or math.isinf(val_float):
                    raise AssetCorrupt("invalid_duration")
                if val_float <= 0:
                    if media_type not in ("video/webm", "audio/webm"):
                        raise AssetCorrupt("invalid_duration")
                    header_duration_ms = None
                else:
                    header_duration_ms = int(round(val_float * 1000))
                    if header_duration_ms > kind_rule["max_duration_ms"]:
                        raise AssetCorrupt("overlong_media")
            except ValueError:
                if media_type not in ("video/webm", "audio/webm"):
                    raise AssetCorrupt("invalid_duration") from None
                header_duration_ms = None
        else:
            if media_type not in ("video/webm", "audio/webm"):
                raise AssetCorrupt("invalid_duration")
            header_duration_ms = None

        ffmpeg_cmd = [
            self.ffmpeg_path,
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
            type_rule["demuxer"],
        ]
        if media_type in ("video/mp4", "audio/mp4"):
            ffmpeg_cmd.extend(["-use_absolute_path", "0", "-enable_drefs", "0"])
        ffmpeg_cmd.extend(
            [
                "-i",
                str(file_path.resolve()),
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
                "-progress",
                "pipe:1",
            ]
        )

        decode_launcher_cmd = self._build_launcher_cmd(ffmpeg_cmd)
        try:
            decode_proc = await asyncio.create_subprocess_exec(
                *decode_launcher_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as err:
            raise StorageUnavailable("Media verification tool unavailable") from err
        except OSError as err:
            raise StorageUnavailable("Failed to execute media verification worker") from err

        assert decode_proc.stdout is not None
        assert decode_proc.stderr is not None
        decode_stdout_reader = decode_proc.stdout
        decode_stderr_reader = decode_proc.stderr

        max_duration_us = kind_rule["max_duration_ms"] * 1000
        last_out_time_us = 0
        overlong = False
        stderr_chunks: list[bytes] = []
        stderr_len = 0
        max_stderr = 65536

        async def _process_stdout() -> None:
            nonlocal last_out_time_us, overlong
            buffer = b""
            while True:
                chunk = await decode_stdout_reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if line.startswith(b"out_time_us="):
                        try:
                            us_val = int(line.split(b"=", 1)[1].strip())
                            if us_val > max_duration_us:
                                overlong = True
                                with contextlib.suppress(Exception):
                                    decode_proc.kill()
                                return
                            last_out_time_us = us_val
                        except ValueError:
                            pass
                if len(buffer) > 16384:
                    with contextlib.suppress(Exception):
                        decode_proc.kill()
                    return

        async def _read_stderr() -> None:
            nonlocal stderr_len
            while True:
                chunk = await decode_stderr_reader.read(4096)
                if not chunk:
                    break
                if stderr_len < max_stderr:
                    keep = chunk[: max_stderr - stderr_len]
                    stderr_chunks.append(keep)
                stderr_len += len(chunk)
                if stderr_len >= max_stderr * 16:
                    with contextlib.suppress(Exception):
                        decode_proc.kill()
                    break

        stdout_task = asyncio.create_task(_process_stdout())
        stderr_task = asyncio.create_task(_read_stderr())

        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, decode_proc.wait()),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as err:
            with contextlib.suppress(Exception):
                decode_proc.kill()
            with contextlib.suppress(Exception):
                await decode_proc.wait()
            raise StorageUnavailable("Media verification service timed out") from err
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                decode_proc.kill()
            with contextlib.suppress(Exception):
                await decode_proc.wait()
            raise
        finally:
            for t in (stdout_task, stderr_task):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            if decode_proc.returncode is None:
                with contextlib.suppress(Exception):
                    decode_proc.kill()
                with contextlib.suppress(Exception):
                    await decode_proc.wait()

        if overlong:
            raise AssetCorrupt("overlong_media")

        if decode_proc.returncode in (-24, 152, -25, 153, -9, 137):
            raise AssetCorrupt("resource_limit_exceeded")
        if decode_proc.returncode in (126, 127):
            raise StorageUnavailable("Media verification tool unavailable")
        if decode_proc.returncode != 0:
            raise AssetCorrupt("corrupt_media")

        stderr_output = b"".join(stderr_chunks)
        if stderr_output.strip():
            raise AssetCorrupt("corrupt_media")

        if last_out_time_us <= 0:
            raise AssetCorrupt("empty_media")

        decoded_duration_ms = int(round(last_out_time_us / 1000))
        if decoded_duration_ms > kind_rule["max_duration_ms"]:
            raise AssetCorrupt("overlong_media")

        if header_duration_ms is not None:
            tolerance = max(2000, int(header_duration_ms * 0.05))
            if abs(header_duration_ms - decoded_duration_ms) > tolerance:
                raise AssetCorrupt("duration_mismatch")
            return header_duration_ms

        return decoded_duration_ms
