import os
import shutil
import stat
import subprocess
from pathlib import Path


def test_startup_creates_private_configuration_once(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).parents[1] / "scripts" / script.name, script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    subprocess.run(["bash", str(script), "up"], env=environment, check=True, cwd="/tmp")
    configuration = tmp_path / ".env.local"
    initial = configuration.read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in initial.splitlines())
    assert all(
        values[key] for key in ("POSTGRES_PASSWORD", "BACKEND_DB_PASSWORD", "AI_DB_PASSWORD")
    )
    assert len(set(values.values())) == len(values)
    assert stat.S_IMODE(configuration.stat().st_mode) == 0o600

    subprocess.run(["bash", str(script), "up"], env=environment, check=True, cwd="/tmp")
    assert configuration.read_text(encoding="utf-8") == initial
