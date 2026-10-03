import os

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-postgres",
        action="store_true",
        help="Require PostgreSQL configuration and fail on skipped persistence tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--require-postgres"):
        return
    for name in ("ASSET_TEST_DATABASE_URL", "PROJECT_TEST_DATABASE_URL"):
        try:
            url = make_url(os.environ.get(name, ""))
        except ArgumentError:
            raise pytest.UsageError(f"{name} must be a disposable PostgreSQL URL") from None
        if url.drivername != "postgresql+asyncpg":
            raise pytest.UsageError(f"{name} must use the postgresql+asyncpg driver")
    config.pluginmanager.register(RequiredPersistenceTests(), "required-persistence-tests")


class RequiredPersistenceTests:
    def __init__(self) -> None:
        self.skipped: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.record_skip(report)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        self.record_skip(report)

    def record_skip(self, report: pytest.TestReport | pytest.CollectReport) -> None:
        if report.skipped and report.nodeid.startswith("tests/integration/persistence/"):
            self.skipped.add(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.skipped:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if self.skipped:
            terminalreporter.write_sep("=", "Required PostgreSQL tests were skipped", red=True)
            for nodeid in sorted(self.skipped):
                terminalreporter.write_line(nodeid, red=True)
