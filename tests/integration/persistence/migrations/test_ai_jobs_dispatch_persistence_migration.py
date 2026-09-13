import json
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


def test_ai_jobs_dispatch_persistence_migration_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_persistence_test.db"
    sync_url = f"sqlite:///{db_path}"
    config = _migration_configuration(sync_url)

    # 1. Upgrade to pre-dispatch revision (table rename 40d664ee8b4d)
    command.upgrade(config, "40d664ee8b4d")

    engine = create_engine(sync_url)
    inspector_before = inspect(engine)
    assert "ai_jobs" in inspector_before.get_table_names()

    col_names_before = [col["name"] for col in inspector_before.get_columns("ai_jobs")]
    new_fields = {
        "payload",
        "queued_at",
        "next_dispatch_at",
        "dispatch_retry_count",
        "last_dispatch_error_category",
        "completed_result",
    }
    for field in new_fields:
        assert field not in col_names_before

    index_names_before = [idx["name"] for idx in inspector_before.get_indexes("ai_jobs")]
    assert "ix_ai_jobs_pending_dispatch" not in index_names_before

    # 2. Insert valid prerequisite rows and an existing AI job prior to migration
    user_id = str(uuid4())
    team_id = str(uuid4())
    project_id = str(uuid4())
    session_id = str(uuid4())
    attempt_id = str(uuid4())
    existing_job_id = str(uuid4())
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
            "VALUES (?, 'Team Beta', '2026-09-14 00:00:00', 1)",
            (team_id,),
        )
        conn.exec_driver_sql(
            "INSERT INTO projects (id, team_id, name, created_at, version) "
            "VALUES (?, ?, 'Project AI', '2026-09-14 00:00:00', 1)",
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
            "INSERT INTO ai_jobs ("
            "id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, last_error, created_at, updated_at"
            ") VALUES (?, ?, ?, 1, 'analyze_session', 'pending', ?, 0, 1, 0, 0, 0, NULL, "
            "'2026-09-14 00:00:00', '2026-09-14 00:00:00')",
            (existing_job_id, session_id, attempt_id, correlation_id),
        )

    # 3. Upgrade to head (dispatch persistence migration f31a1f55d085)
    command.upgrade(config, "head")

    inspector_after = inspect(engine)
    col_names_after = {col["name"]: col for col in inspector_after.get_columns("ai_jobs")}
    for field in new_fields:
        assert field in col_names_after

    # Verify pending dispatch index exists
    indexes_after = {idx["name"]: idx for idx in inspector_after.get_indexes("ai_jobs")}
    assert "ix_ai_jobs_pending_dispatch" in indexes_after
    pending_idx = indexes_after["ix_ai_jobs_pending_dispatch"]
    assert pending_idx["column_names"] == ["status", "next_dispatch_at", "created_at", "id"]

    # 4. Verify existing row survived and has valid defaults
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, payload, queued_at, next_dispatch_at, dispatch_retry_count, "
            "last_dispatch_error_category, completed_result FROM ai_jobs WHERE id = ?",
            (existing_job_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == existing_job_id
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] == 0
        assert row[5] is None
        assert row[6] is None

    # 5. Verify metadata parity between migration and ORM model
    assert "ai_jobs" in Base.metadata.tables
    orm_table = Base.metadata.tables["ai_jobs"]
    orm_cols = {col.name: col for col in orm_table.columns}
    assert set(col_names_after.keys()) == set(orm_cols.keys())
    for col_name, db_col in col_names_after.items():
        assert db_col["nullable"] == orm_cols[col_name].nullable

    # 6. Verify inserting and deterministic querying of eligible pending jobs
    envelope = {
        "version": 1,
        "job_type": "analyze_session",
        "job_id": str(uuid4()),
        "session_id": session_id,
        "attempt_id": attempt_id,
        "correlation_id": str(uuid4()),
    }
    completed_res = {
        "status": "succeeded",
        "overall_score": 85.5,
        "summary": "Solid presentation structure.",
    }

    attempt_a_id = str(uuid4())
    attempt_b_id = str(uuid4())
    job_a_id = str(uuid4())
    job_b_id = str(uuid4())
    t1 = "2026-09-14 01:00:00"
    t2 = "2026-09-14 02:00:00"

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO analysis_attempts (id, session_id, manifest_id, attempt_number, status, "
            "version, created_at) VALUES (?, ?, ?, 2, 'queued', 1, '2026-09-14 01:00:00')",
            (attempt_a_id, session_id, manifest_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO analysis_attempts (id, session_id, manifest_id, attempt_number, status, "
            "version, created_at) VALUES (?, ?, ?, 3, 'queued', 1, '2026-09-14 01:00:00')",
            (attempt_b_id, session_id, manifest_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO ai_jobs ("
            "id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, payload, queued_at, next_dispatch_at, dispatch_retry_count, "
            "last_dispatch_error_category, completed_result, created_at, updated_at"
            ") VALUES (?, ?, ?, 2, 'analyze_session', 'pending', ?, 0, 1, 0, 0, 0, ?, ?, ?, "
            "1, 'transient_timeout', ?, '2026-09-14 01:00:00', '2026-09-14 01:00:00')",
            (
                job_a_id,
                session_id,
                attempt_a_id,
                str(uuid4()),
                json.dumps(envelope),
                t1,
                t2,
                json.dumps(completed_res),
            ),
        )
        conn.exec_driver_sql(
            "INSERT INTO ai_jobs ("
            "id, practice_session_id, attempt_id, analysis_attempt, job_type, status, "
            "correlation_id, last_update_sequence, payload_version, attempts, retry_count, "
            "cancel_requested, payload, queued_at, next_dispatch_at, dispatch_retry_count, "
            "last_dispatch_error_category, completed_result, created_at, updated_at"
            ") VALUES (?, ?, ?, 3, 'analyze_session', 'pending', ?, 0, 1, 0, 0, 0, ?, ?, ?, "
            "0, NULL, NULL, '2026-09-14 00:30:00', '2026-09-14 00:30:00')",
            (
                job_b_id,
                session_id,
                attempt_b_id,
                str(uuid4()),
                json.dumps(envelope),
                t1,
                t1,
            ),
        )

    # Query eligible pending jobs ordered by next_dispatch_at ASC, created_at ASC, id ASC
    with engine.connect() as conn:
        ordered_ids = [
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT id FROM ai_jobs WHERE status = 'pending' AND next_dispatch_at IS NOT NULL "
                "ORDER BY next_dispatch_at ASC, created_at ASC, id ASC"
            ).fetchall()
        ]
        assert ordered_ids == [job_b_id, job_a_id]

        # Verify JSON payload and result retrieval
        row_a = conn.exec_driver_sql(
            "SELECT payload, completed_result, last_dispatch_error_category, "
            "dispatch_retry_count FROM ai_jobs WHERE id = ?",
            (job_a_id,),
        ).fetchone()
        assert row_a is not None
        assert json.loads(row_a[0]) == envelope
        assert json.loads(row_a[1]) == completed_res
        assert row_a[2] == "transient_timeout"
        assert row_a[3] == 1

    # 7. Downgrade back to 40d664ee8b4d
    command.downgrade(config, "40d664ee8b4d")

    inspector_downgrade = inspect(engine)
    col_names_downgrade = [col["name"] for col in inspector_downgrade.get_columns("ai_jobs")]
    for field in new_fields:
        assert field not in col_names_downgrade

    indexes_downgrade = [idx["name"] for idx in inspector_downgrade.get_indexes("ai_jobs")]
    assert "ix_ai_jobs_pending_dispatch" not in indexes_downgrade

    # Verify original row survived downgrade
    with engine.connect() as conn:
        downgraded_row = conn.exec_driver_sql(
            "SELECT id, practice_session_id, attempt_id, analysis_attempt, job_type, status "
            "FROM ai_jobs WHERE id = ?",
            (existing_job_id,),
        ).fetchone()
        assert downgraded_row is not None
        assert downgraded_row[0] == existing_job_id
        assert downgraded_row[5] == "pending"

    engine.dispose()
