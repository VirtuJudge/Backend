from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.infrastructure.database import Base

ROOT = Path(__file__).resolve().parents[4]


def _migration_configuration(sync_url: str) -> Config:
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", sync_url)
    return configuration


def test_ai_jobs_rename_migration_upgrade_data_preservation_and_downgrade(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rename_test.db"
    sync_url = f"sqlite:///{db_path}"
    config = _migration_configuration(sync_url)

    # 1. Upgrade to pre-rename revision
    command.upgrade(config, "ef143c2a901b")

    engine = create_engine(sync_url)
    inspector_before = inspect(engine)
    assert "analysis_jobs" in inspector_before.get_table_names()
    assert "ai_jobs" not in inspector_before.get_table_names()

    index_names_before = [idx["name"] for idx in inspector_before.get_indexes("analysis_jobs")]
    assert "ix_analysis_jobs_correlation_id" in index_names_before
    assert "ix_analysis_jobs_practice_session_id" in index_names_before

    # 2. Insert valid rows prior to upgrade
    user_id = str(uuid4())
    team_id = str(uuid4())
    project_id = str(uuid4())
    session_id = str(uuid4())
    attempt_id = str(uuid4())
    job_id = str(uuid4())
    correlation_id = str(uuid4())

    manifest_id = str(uuid4())

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (id, email, issuer, subject, created_at) "
            "VALUES (?, 'founder@example.com', 'iss', 'sub', '2026-09-14 00:00:00')",
            (user_id,),
        )
        conn.exec_driver_sql(
            "INSERT INTO teams (id, name, created_at, version) "
            "VALUES (?, 'Team Alpha', '2026-09-14 00:00:00', 1)",
            (team_id,),
        )
        conn.exec_driver_sql(
            "INSERT INTO projects (id, team_id, name, created_at, version) "
            "VALUES (?, ?, 'Project Venture', '2026-09-14 00:00:00', 1)",
            (project_id, team_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO practice_sessions (id, project_id, created_by, status, version, "
            "created_at, updated_at, consent_granted) "
            "VALUES (?, ?, ?, 'draft', 1, '2026-09-14 00:00:00', '2026-09-14 00:00:00', 1)",
            (session_id, project_id, user_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO session_manifests ("
            "id, session_id, presentation_version_id, rubric_id, rubric_version"
            ") VALUES (?, ?, ?, 'startup_pitch', 1)",
            (manifest_id, session_id, str(uuid4())),
        )
        conn.exec_driver_sql(
            "INSERT INTO analysis_attempts (id, session_id, manifest_id, attempt_number, status, "
            "version, created_at) "
            "VALUES (?, ?, ?, 1, 'queued', 1, '2026-09-14 00:00:00')",
            (attempt_id, session_id, manifest_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO analysis_jobs ("
            "id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, last_error, created_at, updated_at"
            ") VALUES (?, ?, ?, 1, 'analyze_session', 'pending', ?, 3, 1, 1, 0, 0, "
            "'sample error', '2026-09-14 00:00:00', '2026-09-14 00:01:00')",
            (job_id, session_id, attempt_id, correlation_id),
        )

    # 3. Upgrade to head (rename migration 40d664ee8b4d)
    command.upgrade(config, "head")

    inspector_after = inspect(engine)
    assert "ai_jobs" in inspector_after.get_table_names()
    assert "analysis_jobs" not in inspector_after.get_table_names()

    index_names_after = [idx["name"] for idx in inspector_after.get_indexes("ai_jobs")]
    assert "ix_ai_jobs_correlation_id" in index_names_after
    assert "ix_ai_jobs_practice_session_id" in index_names_after
    assert "ix_analysis_jobs_correlation_id" not in index_names_after
    assert "ix_analysis_jobs_practice_session_id" not in index_names_after

    # 4. Verify data survived upgrade intact
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, last_error FROM ai_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == job_id
        assert row[1] == session_id
        assert row[2] == attempt_id
        assert row[3] == 1
        assert row[4] == "analyze_session"
        assert row[5] == "pending"
        assert row[6] == correlation_id
        assert row[7] == 3
        assert row[8] == 1
        assert row[9] == 1
        assert row[10] == 0
        assert row[11] == 0
        assert row[12] == "sample error"

    # 5. Metadata parity verification
    assert "ai_jobs" in Base.metadata.tables
    orm_table = Base.metadata.tables["ai_jobs"]
    db_cols = {col["name"]: col for col in inspector_after.get_columns("ai_jobs")}
    orm_cols = {col.name: col for col in orm_table.columns}
    assert set(db_cols.keys()) == set(orm_cols.keys())
    for col_name, db_col in db_cols.items():
        assert db_col["nullable"] == orm_cols[col_name].nullable

    # 6. Downgrade back to ef143c2a901b
    command.downgrade(config, "ef143c2a901b")

    inspector_downgrade = inspect(engine)
    assert "analysis_jobs" in inspector_downgrade.get_table_names()
    assert "ai_jobs" not in inspector_downgrade.get_table_names()

    index_names_downgrade = [
        idx["name"] for idx in inspector_downgrade.get_indexes("analysis_jobs")
    ]
    assert "ix_analysis_jobs_correlation_id" in index_names_downgrade
    assert "ix_analysis_jobs_practice_session_id" in index_names_downgrade
    assert "ix_ai_jobs_correlation_id" not in index_names_downgrade
    assert "ix_ai_jobs_practice_session_id" not in index_names_downgrade

    # 7. Verify data survived downgrade intact
    with engine.connect() as conn:
        row_downgrade = conn.exec_driver_sql(
            "SELECT id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, last_error FROM analysis_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        assert row_downgrade is not None
        assert row_downgrade[0] == job_id
        assert row_downgrade[1] == session_id
        assert row_downgrade[2] == attempt_id
        assert row_downgrade[3] == 1
        assert row_downgrade[4] == "analyze_session"
        assert row_downgrade[5] == "pending"
        assert row_downgrade[6] == correlation_id
        assert row_downgrade[7] == 3
        assert row_downgrade[8] == 1
        assert row_downgrade[9] == 1
        assert row_downgrade[10] == 0
        assert row_downgrade[11] == 0
        assert row_downgrade[12] == "sample error"

    engine.dispose()
