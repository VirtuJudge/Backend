import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
APP_ROOT = ROOT / "app"

LAYER_RULES = {
    "domain": {
        "alembic",
        "app.api",
        "app.application",
        "app.infrastructure",
        "boto3",
        "botocore",
        "celery",
        "fastapi",
        "google",
        "minio",
        "openai",
        "pydantic",
        "redis",
        "sqlalchemy",
    },
    "application": {
        "alembic",
        "app.api",
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
    },
    "api": {
        "app.infrastructure",
        "boto3",
        "botocore",
        "celery",
        "google",
        "minio",
        "openai",
        "redis",
        "sqlalchemy",
    },
    "infrastructure": {"app.api"},
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def forbidden_imports(layer: str, path: Path) -> set[str]:
    forbidden = LAYER_RULES[layer]
    return {
        module
        for module in imported_modules(path)
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
    }


def test_layer_import_boundaries() -> None:
    violations: list[str] = []
    for layer in LAYER_RULES:
        for path in (APP_ROOT / layer).rglob("*.py"):
            for module in sorted(forbidden_imports(layer, path)):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")

    assert not violations, "Forbidden layer imports:\n" + "\n".join(violations)


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
    ],
)
def test_forbidden_imports_are_detected(
    tmp_path: Path, layer: str, source: str, expected: str
) -> None:
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")

    assert forbidden_imports(layer, path) == {expected}
