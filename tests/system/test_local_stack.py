import os
import shutil
import stat
import subprocess
from pathlib import Path


def create_synthetic_peers(parent_dir: Path) -> tuple[Path, Path]:
    frontend = parent_dir / "Frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "package.json").write_text('{"name": "virtu-judge"}', encoding="utf-8")
    (frontend / "package-lock.json").write_text('{"name": "virtu-judge"}', encoding="utf-8")

    aiml = parent_dir / "AI-ML"
    aiml.mkdir(parents=True, exist_ok=True)
    (aiml / "pyproject.toml").write_text('[project]\nname = "virtujudge-ai-ml"\n', encoding="utf-8")
    (aiml / "app").mkdir(parents=True, exist_ok=True)
    worker_stub = "async def process_job(*args):\n    pass\n"
    (aiml / "app" / "worker.py").write_text(worker_stub, encoding="utf-8")
    return frontend, aiml


def test_startup_creates_private_configuration_once(tmp_path: Path) -> None:
    create_synthetic_peers(tmp_path)
    root = tmp_path / "Backend"
    root.mkdir(exist_ok=True)
    scripts = root / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts" / script.name, script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    subprocess.run(["bash", str(script), "up"], env=environment, check=True, cwd="/tmp")
    configuration = root / ".env.local"
    initial = configuration.read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in initial.splitlines())
    assert all(
        values[key] for key in ("POSTGRES_PASSWORD", "BACKEND_DB_PASSWORD", "AI_DB_PASSWORD")
    )
    assert len(set(values.values())) == len(values)
    assert stat.S_IMODE(configuration.stat().st_mode) == 0o600

    subprocess.run(["bash", str(script), "up"], env=environment, check=True, cwd="/tmp")
    assert configuration.read_text(encoding="utf-8") == initial


def test_startup_fails_with_actionable_error_when_frontend_missing(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "Frontend"
    if frontend_dir.exists():
        shutil.rmtree(frontend_dir)
    aiml_dir = tmp_path / "AI-ML"
    aiml_dir.mkdir(parents=True, exist_ok=True)
    (aiml_dir / "pyproject.toml").write_text(
        '[project]\nname = "virtujudge-ai-ml"\n', encoding="utf-8"
    )
    (aiml_dir / "app").mkdir(parents=True, exist_ok=True)
    (aiml_dir / "app" / "worker.py").write_text(
        "async def process_job(*args):\n    pass\n", encoding="utf-8"
    )

    root = tmp_path / "Backend"
    root.mkdir(exist_ok=True)
    scripts = root / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts" / script.name, script)

    res = subprocess.run(["bash", str(script), "up"], capture_output=True, text=True, cwd="/tmp")
    assert res.returncode != 0
    assert "Frontend" in res.stderr
    assert "package.json" in res.stderr


def test_startup_fails_with_actionable_error_when_aiml_missing(tmp_path: Path) -> None:
    aiml_dir = tmp_path / "AI-ML"
    if aiml_dir.exists():
        shutil.rmtree(aiml_dir)
    frontend_dir = tmp_path / "Frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    (frontend_dir / "package.json").write_text('{"name": "virtu-judge"}', encoding="utf-8")
    (frontend_dir / "package-lock.json").write_text('{"name": "virtu-judge"}', encoding="utf-8")

    root = tmp_path / "Backend"
    root.mkdir(exist_ok=True)
    scripts = root / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts" / script.name, script)

    res = subprocess.run(["bash", str(script), "up"], capture_output=True, text=True, cwd="/tmp")
    assert res.returncode != 0
    assert "AI-ML" in res.stderr
    assert "pyproject.toml" in res.stderr
