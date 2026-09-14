import asyncio
import contextlib
import json
import math
import shutil
from pathlib import Path

from app.application.ports.media_verifier import MediaVerifierPort
from app.domain.asset import AssetCorrupt, StorageUnavailable
from app.infrastructure.media.container import (
    MEDIA_RULES,
)
from app.infrastructure.media.process import (
    build_launcher_cmd,
    run_bounded_process,
)


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
        return build_launcher_cmd(
            tool_cmd=tool_cmd,
            max_memory_mb=self.max_memory_mb,
            max_cpu_seconds=self.max_cpu_seconds,
            max_fsize_mb=self.max_fsize_mb,
        )

    async def _run_bounded_process(
        self,
        cmd: list[str],
        max_stdout: int = 65536,
        max_stderr: int = 65536,
    ) -> tuple[int, bytes, bytes]:
        return await run_bounded_process(
            cmd=cmd,
            timeout_seconds=self.timeout_seconds,
            max_stdout=max_stdout,
            max_stderr=max_stderr,
        )

    async def verify_media(self, file_path: Path, media_type: str, kind: str) -> int:
        clean_media_type = media_type.split(";")[0].strip().lower()
        kind_rule = MEDIA_RULES.get(kind)
        if kind_rule is None:
            raise AssetCorrupt("unsupported_media_kind")

        type_rule = kind_rule["media_types"].get(clean_media_type)
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


__all__ = ["FFmpegMediaVerifier"]
