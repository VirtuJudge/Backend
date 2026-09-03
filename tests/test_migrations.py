from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).parents[1]


def test_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migrations.db"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option(
        "version_locations", str(ROOT / "tests" / "migration_fixtures" / "versions")
    )
    configuration.set_main_option("sqlalchemy.url", async_url)

    command.upgrade(configuration, "head")

    engine = create_engine(sync_url)
    assert "migration_test" in inspect(engine).get_table_names()

    command.downgrade(configuration, "base")
    assert "migration_test" not in inspect(engine).get_table_names()
    engine.dispose()
