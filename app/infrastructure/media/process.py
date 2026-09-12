import asyncio
import contextlib
import sys
from pathlib import Path

from app.domain.asset import AssetCorrupt, StorageUnavailable

LAUNCHER_PATH = Path(__file__).parent / "worker_launcher.py"


def build_launcher_cmd(
    tool_cmd: list[str],
    max_memory_mb: int,
    max_cpu_seconds: int,
    max_fsize_mb: int,
) -> list[str]:
    return [
        sys.executable,
        str(LAUNCHER_PATH.resolve()),
        "--as-mb",
        str(max_memory_mb),
        "--cpu-s",
        str(max_cpu_seconds),
        "--fsize-mb",
        str(max_fsize_mb),
        "--",
        *tool_cmd,
    ]


async def run_bounded_process(
    cmd: list[str],
    timeout_seconds: float,
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
            timeout=timeout_seconds,
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
