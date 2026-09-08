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
    version_columns = [c["name"] for c in inspect(engine).get_columns("asset_versions")]
    assert "upload_expires_at" in version_columns
    assert "cleanup_next_attempt_at" in version_columns

    command.downgrade(configuration, "base")
    tables_downgraded = inspect(engine).get_table_names()
    assert "assets" not in tables_downgraded
    engine.dispose()


def test_migration_backfill_existing_versions(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy_backfill.db"
    sync_url = f"sqlite:///{database_path}"
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", sync_url)

    # Upgrade up to c4d5e6f7a8b9 (prior to cleanup migration)
    command.upgrade(configuration, "c4d5e6f7a8b9")

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (id, email, issuer, subject, created_at) "
            "VALUES ('u1', 'test@example.com', 'iss', 'sub', '2026-09-07 10:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO teams (id, name, created_at, version) "
            "VALUES ('t1', 'Team 1', '2026-09-07 10:00:00', 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects (id, team_id, name, created_at, version) "
            "VALUES ('p1', 't1', 'Proj 1', '2026-09-07 10:00:00', 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO assets (id, project_id, kind, state, file_name, created_by, created_at) "
            "VALUES ('a1', 'p1', 'supporting_document', 'pending_upload', "
            "'doc.pdf', 'u1', '2026-09-07 10:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO asset_versions ("
            "id, asset_id, version_number, state, storage_key, file_name, "
            "declared_media_type, declared_size_bytes, created_by, created_at) "
            "VALUES ('v1', 'a1', 1, 'pending_upload', 'key1', 'doc.pdf', "
            "'application/pdf', 100, 'u1', '2026-09-07 10:00:00')"
        )

    # Now upgrade to head
    command.upgrade(configuration, "head")

    with engine.connect() as conn:
        query = "SELECT upload_expires_at FROM asset_versions WHERE id = 'v1'"
        row = conn.exec_driver_sql(query).fetchone()
        assert row is not None
        assert row[0] is not None

    command.downgrade(configuration, "c4d5e6f7a8b9")
    with engine.connect() as conn:
        cols = [c["name"] for c in inspect(engine).get_columns("asset_versions")]
        assert "upload_expires_at" not in cols

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
