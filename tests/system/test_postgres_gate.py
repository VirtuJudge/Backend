from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
POSTGRES_URL = "postgresql+asyncpg://test:test@localhost/test"


@pytest.fixture
def gate(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setenv("ASSET_TEST_DATABASE_URL", POSTGRES_URL)
    monkeypatch.setenv("PROJECT_TEST_DATABASE_URL", POSTGRES_URL)
    pytester.makeconftest('pytest_plugins = ["tests.support.postgres_gate"]')
    pytester.makepyfile(test_unit="def test_pass(): pass")
    return pytester


@pytest.mark.parametrize("name", ["ASSET_TEST_DATABASE_URL", "PROJECT_TEST_DATABASE_URL"])
@pytest.mark.parametrize("url", [None, "", "not-a-url", "sqlite+aiosqlite:///test.db"])
def test_required_gate_rejects_missing_or_non_postgres_configuration(
    gate: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, name: str, url: str | None
) -> None:
    if url is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, url)
    result = gate.runpytest_subprocess("--require-postgres")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert name in result.stderr.str()


@pytest.mark.parametrize(
    "source",
    [
        'import pytest\ndef test_required(): pytest.skip("missing dependency")',
        'import pytest\n@pytest.mark.skip(reason="missing dependency")\ndef test_required(): pass',
        'import pytest\npytest.skip("missing dependency", allow_module_level=True)',
    ],
)
def test_required_gate_fails_on_runtime_marker_and_collection_skips(
    gate: pytest.Pytester, source: str
) -> None:
    directory = gate.path / "tests/integration/persistence"
    directory.mkdir(parents=True)
    (directory / "test_required.py").write_text(source)
    result = gate.runpytest_subprocess("--require-postgres")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "Required PostgreSQL tests were skipped" in result.stdout.str()
    assert "tests/integration/persistence/test_required.py" in result.stdout.str()


def test_required_gate_allows_optional_cross_repository_skip(gate: pytest.Pytester) -> None:
    directory = gate.path / "tests/acceptance"
    directory.mkdir(parents=True)
    (directory / "test_optional.py").write_text(
        'import pytest\ndef test_optional(): pytest.skip("AI-ML environment unavailable")'
    )
    result = gate.runpytest_subprocess("--require-postgres")
    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_focused_tests_do_not_require_postgres(
    gate: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASSET_TEST_DATABASE_URL")
    monkeypatch.delenv("PROJECT_TEST_DATABASE_URL")
    directory = gate.path / "tests/integration/persistence"
    directory.mkdir(parents=True)
    (directory / "test_optional.py").write_text(
        'import pytest\ndef test_optional(): pytest.skip("PostgreSQL unavailable")'
    )
    result = gate.runpytest_subprocess()
    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_required_gate_passes_when_persistence_tests_run(gate: pytest.Pytester) -> None:
    directory = gate.path / "tests/integration/persistence"
    directory.mkdir(parents=True)
    (directory / "test_required.py").write_text("def test_required(): pass")
    result = gate.runpytest_subprocess("--require-postgres")
    result.assert_outcomes(passed=2)
    assert result.ret == pytest.ExitCode.OK
