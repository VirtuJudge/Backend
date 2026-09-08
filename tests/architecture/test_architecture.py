import ast
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"

EXTERNAL_SDK_RULES = {
    "alembic",
    "boto3",
    "botocore",
    "celery",
    "fastapi",
    "google",
    "minio",
    "openai",
    "redis",
    "sqlalchemy",
}

LAYER_RULES: dict[str, set[str]] = {
    "domain": {"app.application", "app.api", "app.infrastructure", "pydantic"} | EXTERNAL_SDK_RULES,
    "application": {"app.api", "app.infrastructure"} | EXTERNAL_SDK_RULES,
    "application/interfaces": {
        "app.application.services",
        "app.api",
        "app.infrastructure",
    }
    | EXTERNAL_SDK_RULES,
    "api": {"app.infrastructure", *(EXTERNAL_SDK_RULES - {"fastapi"})},
    "infrastructure": {"app.api"},
}

PORTS_FORBIDDEN = {
    "alembic",
    "app.api",
    "app.application.services",
    "app.infrastructure",
    "boto3",
    "botocore",
    "celery",
    "fastapi",
    "google",
    "minio",
    "openai",
    "redis",
    "sqlalchemy",
}


class Violation(NamedTuple):
    source_path: Path
    imported_module: str
    rule: str

    def __str__(self) -> str:
        return f"{self.source_path} imports {self.imported_module} ({self.rule})"


def resolve_import(package_parts: tuple[str, ...], node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        if not node.module:
            return []
        if node.module == "app":
            return [f"app.{name.name}" for name in node.names]
        return [node.module]
    base = package_parts[: max(0, len(package_parts) - (node.level - 1))]
    mod_parts = tuple(node.module.split(".")) if node.module else ()
    target = base + mod_parts
    if not target or target[0] in ("domain", "application", "api", "infrastructure"):
        target = ("app",) + target
    if node.module:
        return [".".join(target)]
    return [".".join(target + (name.name,)) for name in node.names]


def layer_for_path(path: Path) -> str | None:
    parts = path.parts
    if "domain" in parts:
        return "domain"
    if "application" in parts:
        return (
            "application/interfaces"
            if any(p in ("interfaces", "ports") for p in parts)
            else "application"
        )
    if any(p in ("interfaces", "ports") for p in parts):
        return "application/interfaces"
    if "api" in parts:
        return "api"
    if "infrastructure" in parts:
        return "infrastructure"
    return None


def package_for_path(path: Path, layer: str | None = None) -> tuple[str, ...]:
    parts = path.parts
    if "app" in parts:
        idx = parts.index("app")
        return parts[idx : len(parts) - 1]
    for name in ("domain", "application", "api", "infrastructure"):
        if name in parts:
            idx = parts.index(name)
            return ("app",) + parts[idx : len(parts) - 1]
    defaults = {
        "domain": ("app", "domain"),
        "application": ("app", "application"),
        "application/interfaces": ("app", "application", "interfaces"),
        "api": ("app", "api"),
        "infrastructure": ("app", "infrastructure"),
    }
    return defaults.get(layer or "", ("app",))


def find_violations(layer: str, path: Path, base: Path | None = None) -> list[Violation]:
    src = path.relative_to(base) if base and path.is_relative_to(base) else path
    src = src.relative_to(ROOT) if src.is_relative_to(ROOT) else src
    pkg = package_for_path(path, layer)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.extend(resolve_import(pkg, node))

    violations: list[Violation] = []
    for mod in modules:
        for prefix in LAYER_RULES.get(layer, ()):
            if mod == prefix or mod.startswith(f"{prefix}."):
                violations.append(Violation(src, mod, f"{layer} cannot import {prefix}"))
                break
    return violations


def forbidden_imports(layer: str, path: Path) -> set[str]:
    return {v.imported_module for v in find_violations(layer, path)}


def imported_modules(path: Path) -> set[str]:
    package = package_for_path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.update(resolve_import(package, node))
    return modules


def scan_source_tree(root: Path) -> list[Violation]:
    app_root = root / "app" if (root / "app").is_dir() else root
    base = root if (root / "app").is_dir() else app_root
    violations: list[Violation] = []
    for path in sorted(app_root.rglob("*.py")):
        layer = layer_for_path(path)
        if layer:
            violations.extend(find_violations(layer, path, base))
    return violations


def test_layer_import_boundaries() -> None:
    violations = scan_source_tree(APP_ROOT)
    assert not violations, "Forbidden layer imports:\n" + "\n".join(str(v) for v in violations)


def test_ports_do_not_import_outward() -> None:
    ports_dir = APP_ROOT / "application" / "ports"
    assert ports_dir.is_dir()
    violations: list[str] = []
    for path in ports_dir.rglob("*.py"):
        modules = imported_modules(path)
        forbidden = {
            m
            for m in modules
            if any(m == prefix or m.startswith(f"{prefix}.") for prefix in PORTS_FORBIDDEN)
        }
        for m in sorted(forbidden):
            violations.append(f"{path.relative_to(ROOT)} imports {m}")

    assert not violations, "Forbidden port imports:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("layer", "source", "expected"),
    [
        ("domain", "from fastapi import FastAPI", "fastapi"),
        ("domain", "from sqlalchemy.orm import Mapped", "sqlalchemy.orm"),
        (
            "application",
            "from app.infrastructure.database import metadata",
            "app.infrastructure.database",
        ),
        ("api", "from app.infrastructure.database import metadata", "app.infrastructure.database"),
        # Relative outward imports
        (
            "domain",
            "from ..infrastructure.database import metadata",
            "app.infrastructure.database",
        ),
        (
            "domain",
            "from ..application.services.user_service import UserService",
            "app.application.services.user_service",
        ),
        (
            "domain",
            "from ..api.routes import user",
            "app.api.routes",
        ),
        (
            "application",
            "from ..infrastructure.database import metadata",
            "app.infrastructure.database",
        ),
        (
            "application",
            "from ..api.routes import user",
            "app.api.routes",
        ),
        (
            "api",
            "from ...infrastructure.database import metadata",
            "app.infrastructure.database",
        ),
        (
            "infrastructure",
            "from ...api.routes import user",
            "app.api.routes",
        ),
        (
            "infrastructure",
            "from app.api.routes import user",
            "app.api.routes",
        ),
        # Application interfaces / ports boundaries
        (
            "application/interfaces",
            "from app.application.services.user_service import UserService",
            "app.application.services.user_service",
        ),
        (
            "application/interfaces",
            "from ..services.user_service import UserService",
            "app.application.services.user_service",
        ),
        (
            "application/interfaces",
            "from app.infrastructure.database import metadata",
            "app.infrastructure.database",
        ),
        (
            "application/interfaces",
            "from fastapi import FastAPI",
            "fastapi",
        ),
    ],
)
def test_forbidden_imports_are_detected(
    tmp_path: Path, layer: str, source: str, expected: str
) -> None:
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")

    assert forbidden_imports(layer, path) == {expected}


@pytest.mark.parametrize(
    ("layer", "source"),
    [
        ("domain", "from .project import Project\nfrom dataclasses import dataclass"),
        ("application", "from app.domain.user import User\nfrom dataclasses import dataclass"),
        ("application", "from ..domain.user import User"),
        ("application", "from app.application.ports.user_repository import UserRepository"),
        ("application/interfaces", "from app.domain.user import User\nfrom abc import ABC"),
        ("application/interfaces", "from ...domain.user import User"),
        ("application/interfaces", "from .teamRepository import TeamRepository"),
        ("api", "from app.domain.user import User\nfrom fastapi import APIRouter"),
        ("api", "from app.application.services.user_service import UserService"),
        ("infrastructure", "from app.domain.user import User\nimport sqlalchemy"),
        ("infrastructure", "from app.application.ports.user_repository import UserRepository"),
    ],
)
def test_allowed_inward_imports_are_accepted(tmp_path: Path, layer: str, source: str) -> None:
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")

    assert forbidden_imports(layer, path) == set()


def test_diagnostics_and_highest_seam(tmp_path: Path) -> None:
    assert not scan_source_tree(APP_ROOT)

    synthetic = tmp_path / "synthetic_app"
    files = {
        synthetic
        / "domain"
        / "bad.py": "import fastapi\nfrom ..infrastructure.database import metadata\n",
        synthetic / "domain" / "good.py": "from .project import Project\n",
        synthetic
        / "application"
        / "interfaces"
        / "bad.py": "from ..services.user_service import UserService\n",
        synthetic
        / "application"
        / "ports"
        / "bad.py": "from app.application.services.user_service import UserService\n",
        synthetic / "application" / "interfaces" / "good.py": "from app.domain.user import User\n",
        synthetic / "api" / "bad.py": "from app.infrastructure.database import metadata\n",
        synthetic / "infrastructure" / "bad.py": "from app.api.routes import user\n",
    }
    for file_path, code in files.items():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")

    violations = scan_source_tree(synthetic)
    assert len(violations) == 6
    for v in violations:
        assert v.source_path and v.imported_module and v.rule
        assert str(v.source_path) in str(v)
        assert v.imported_module in str(v)
        assert v.rule in str(v)

    violation_modules = {v.imported_module for v in violations}
    assert violation_modules == {
        "fastapi",
        "app.infrastructure.database",
        "app.application.services.user_service",
        "app.api.routes",
    }
