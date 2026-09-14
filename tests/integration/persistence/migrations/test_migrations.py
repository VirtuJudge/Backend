import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, make_url

ROOT = Path(__file__).resolve().parents[4]


def migration_configuration(sync_url: str) -> Config:
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", sync_url)
    return configuration


def test_backend_migrations_ignore_a_foreign_alembic_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "shared-migrations.db"
    sync_url = f"sqlite:///{database_path}"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('d0bab208d7c4')"
        )

    command.upgrade(migration_configuration(sync_url), "head")

    with engine.connect() as connection:
        foreign_revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
        backend_revision = connection.exec_driver_sql(
            "SELECT version_num FROM backend_alembic_version"
        ).scalar_one()

    assert foreign_revision == "d0bab208d7c4"
    assert backend_revision == "f31a1f55d085"
    assert "users" in inspect(engine).get_table_names()
    engine.dispose()


def test_backend_migrations_adopt_an_existing_legacy_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-backend-schema.db"
    sync_url = f"sqlite:///{database_path}"
    configuration = migration_configuration(sync_url)

    command.upgrade(configuration, "c210932aac79")

    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE backend_alembic_version")
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('d0bab208d7c4')"
        )

    command.upgrade(configuration, "head")

    with engine.connect() as connection:
        foreign_revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
        backend_revision = connection.exec_driver_sql(
            "SELECT version_num FROM backend_alembic_version"
        ).scalar_one()

    assert foreign_revision == "d0bab208d7c4"
    assert backend_revision == "f31a1f55d085"
    assert "practice_sessions" in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migrations.db"
    sync_url = f"sqlite:///{database_path}"
    configuration = migration_configuration(sync_url)

    command.upgrade(configuration, "head")

    engine = create_engine(sync_url)
    tables = inspect(engine).get_table_names()
    assert "assets" in tables
    assert "asset_versions" in tables
    assert "asset_upload_idempotency" in tables
    assert "practice_sessions" in tables
    assert "session_manifests" in tables
    assert "session_manifest_documents" in tables
    assert "session_command_idempotency" in tables
    assert "analysis_attempts" in tables
    assert "ai_jobs" in tables
    assert "analysis_jobs" not in tables
    assert "analysis_stages" in tables
    assert "speaker_mappings" in tables
    manifest_columns = [c["name"] for c in inspect(engine).get_columns("session_manifests")]
    assert "rubric_id" in manifest_columns
    assert "rubric_version" in manifest_columns
    assert "snapshot" in manifest_columns
    job_columns = [c["name"] for c in inspect(engine).get_columns("ai_jobs")]
    assert "practice_session_id" in job_columns
    assert "analysis_attempt" in job_columns
    assert "job_type" in job_columns
    assert "last_update_sequence" in job_columns
    assert "payload_version" in job_columns
    assert "attempts" in job_columns
    assert "cancel_requested" in job_columns
    assert "payload" in job_columns
    assert "queued_at" in job_columns
    assert "next_dispatch_at" in job_columns
    assert "dispatch_retry_count" in job_columns
    assert "last_dispatch_error_category" in job_columns
    assert "completed_result" in job_columns
    asset_columns = [c["name"] for c in inspect(engine).get_columns("assets")]
    assert "retention_expires_at" in asset_columns
    version_columns = [c["name"] for c in inspect(engine).get_columns("asset_versions")]
    assert "upload_expires_at" in version_columns
    assert "cleanup_next_attempt_at" in version_columns
    session_columns = [c["name"] for c in inspect(engine).get_columns("practice_sessions")]
    assert "consent_policy_version" in session_columns
    assert "consent_confirmed_by" in session_columns
    assert "cancelled_by" in session_columns
    assert "cancellation_reason" in session_columns
    invitation_columns = {
        column["name"]: column for column in inspect(engine).get_columns("team_invitations")
    }
    assert "version" in invitation_columns
    assert invitation_columns["version"]["nullable"] is False
    invitation_foreign_keys = inspect(engine).get_foreign_keys("team_invitations")
    assert any(
        foreign_key["referred_table"] == "teams"
        and foreign_key["constrained_columns"] == ["team_id"]
        for foreign_key in invitation_foreign_keys
    )

    command.downgrade(configuration, "base")
    tables_downgraded = inspect(engine).get_table_names()
    assert "assets" not in tables_downgraded
    assert "practice_sessions" not in tables_downgraded
    assert "ai_jobs" not in tables_downgraded
    assert "analysis_jobs" not in tables_downgraded
    engine.dispose()


def test_migration_backfill_existing_versions(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy_backfill.db"
    sync_url = f"sqlite:///{database_path}"
    configuration = migration_configuration(sync_url)

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

    configuration = migration_configuration(pg_url)

    command.upgrade(configuration, "head")

    sync_url = (
        make_url(pg_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    )
    engine = create_engine(sync_url)
    tables = inspect(engine).get_table_names()
    assert "assets" in tables
    assert "asset_versions" in tables
    assert "asset_upload_idempotency" in tables
    assert "ai_jobs" in tables
    assert "analysis_jobs" not in tables

    command.downgrade(configuration, "a1b2c3d4e5f6")
    tables_downgraded = inspect(engine).get_table_names()
    assert "assets" not in tables_downgraded
    assert "ai_jobs" not in tables_downgraded
    assert "analysis_jobs" not in tables_downgraded

    command.upgrade(configuration, "head")
    tables_final = inspect(engine).get_table_names()
    assert "assets" in tables_final
    assert "ai_jobs" in tables_final
    assert "analysis_jobs" not in tables_final
    engine.dispose()
