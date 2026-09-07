import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, make_url

ROOT = Path(__file__).parents[1]


def test_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migrations.db"
    sync_url = f"sqlite:///{database_path}"
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", sync_url)

    command.upgrade(configuration, "head")

    engine = create_engine(sync_url)
    tables = inspect(engine).get_table_names()
    assert "assets" in tables
    assert "asset_versions" in tables
    assert "asset_upload_idempotency" in tables
    asset_columns = [c["name"] for c in inspect(engine).get_columns("assets")]
    assert "retention_expires_at" in asset_columns

    command.downgrade(configuration, "base")
    tables_downgraded = inspect(engine).get_table_names()
    assert "assets" not in tables_downgraded
    engine.dispose()


def test_postgres_migration_upgrade_and_downgrade() -> None:
    pg_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if not pg_url:
        pytest.skip("ASSET_TEST_DATABASE_URL not configured")

    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", pg_url)

    command.upgrade(configuration, "head")

    sync_url = (
        make_url(pg_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    )
    engine = create_engine(sync_url)
    tables = inspect(engine).get_table_names()
    assert "assets" in tables
    assert "asset_versions" in tables
    assert "asset_upload_idempotency" in tables

    command.downgrade(configuration, "a1b2c3d4e5f6")
    tables_downgraded = inspect(engine).get_table_names()
    assert "assets" not in tables_downgraded

    command.upgrade(configuration, "head")
    tables_final = inspect(engine).get_table_names()
    assert "assets" in tables_final
    engine.dispose()
