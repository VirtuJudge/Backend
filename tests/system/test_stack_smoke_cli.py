import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from local_stack.smoke import main


def test_main_cli_failure_hides_connection_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic")
    with patch("psycopg.connect", side_effect=RuntimeError("private connection details")):
        assert main(["backend"]) == 1
    assert "private connection details" not in capsys.readouterr().err


def test_local_stack_sh_smoke_calls_docker_exec(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts" / script.name, script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    log_file = tmp_path / "docker_cmds.log"
    docker.write_text(f'#!/bin/sh\necho "$@" >> "{log_file}"\nexit 0\n', encoding="utf-8")
    docker.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    # 1. Default smoke runs all (backend and ai)
    subprocess.run(
        ["bash", str(script), "smoke"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T backend python -m local_stack.smoke backend" in content
    assert "exec -T ai-worker python -m local_stack.smoke ai" in content

    # 2. smoke backend runs only backend
    log_file.unlink()
    subprocess.run(
        ["bash", str(script), "smoke", "backend"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T backend python -m local_stack.smoke backend" in content
    assert "ai-worker" not in content

    # 3. smoke ai runs only ai
    log_file.unlink()
    subprocess.run(
        ["bash", str(script), "smoke", "ai"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T ai-worker python -m local_stack.smoke ai" in content
    assert "backend" not in content
