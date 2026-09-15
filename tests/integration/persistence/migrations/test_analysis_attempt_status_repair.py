import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "a4f6c8e1d2b3_repair_analysis_attempt_status_enum.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analysis_attempt_status_repair", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bind_with_labels(*labels: str) -> MagicMock:
    bind = MagicMock()
    bind.dialect = SimpleNamespace(name="postgresql")
    bind.execute.return_value = [(label,) for label in labels]
    return bind


def test_repairs_legacy_pending_label() -> None:
    migration = _migration_module()

    with patch.object(migration.op, "execute") as execute:
        migration.repair_analysis_attempt_status_enum(
            _bind_with_labels("pending", "running", "failed", "completed", "cancelled")
        )

    execute.assert_called_once_with(
        "ALTER TYPE analysisattemptstatus RENAME VALUE 'pending' TO 'queued'"
    )


def test_leaves_canonical_queued_label_unchanged() -> None:
    migration = _migration_module()

    with patch.object(migration.op, "execute") as execute:
        migration.repair_analysis_attempt_status_enum(
            _bind_with_labels("queued", "running", "failed", "completed", "cancelled")
        )

    execute.assert_not_called()


def test_normalizes_rows_when_both_labels_exist() -> None:
    migration = _migration_module()

    with patch.object(migration.op, "execute") as execute:
        migration.repair_analysis_attempt_status_enum(
            _bind_with_labels("pending", "queued", "running", "failed", "completed", "cancelled")
        )

    execute.assert_called_once_with(
        "UPDATE analysis_attempts SET status = 'queued' WHERE status = 'pending'"
    )
