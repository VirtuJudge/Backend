from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import String, UniqueConstraint, create_engine, inspect

from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations import (  # noqa: F401
    AnalysisAttemptModel,
    AnalysisJobModel,
    AnalysisStageModel,
    PracticeSessionModel,
    SessionCommandIdempotencyModel,
    SessionManifestDocumentModel,
    SessionManifestModel,
    SpeakerMappingModel,
)

ROOT = Path(__file__).resolve().parents[3]


def _migration_configuration(sync_url: str) -> Config:
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", sync_url)
    return configuration


def test_migration_metadata_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "parity_check.db"
    sync_url = f"sqlite:///{db_path}"
    config = _migration_configuration(sync_url)
    command.upgrade(config, "head")

    engine = create_engine(sync_url)
    inspector = inspect(engine)

    target_tables = [
        "analysis_attempts",
        "session_command_idempotency",
        "session_manifest_documents",
        "analysis_jobs",
        "analysis_stages",
        "speaker_mappings",
    ]

    for table_name in target_tables:
        assert table_name in Base.metadata.tables
        orm_table = Base.metadata.tables[table_name]
        db_cols = {col["name"]: col for col in inspector.get_columns(table_name)}
        orm_cols = {col.name: col for col in orm_table.columns}

        assert set(db_cols.keys()) == set(orm_cols.keys()), f"Column mismatch in {table_name}"
        for col_name, db_col in db_cols.items():
            orm_col = orm_cols[col_name]
            assert db_col["nullable"] == orm_col.nullable, (
                f"Nullability mismatch on {table_name}.{col_name}"
            )

    # Specific assertions for analysis_attempts
    attempt_table = Base.metadata.tables["analysis_attempts"]
    attempt_key_type = attempt_table.columns["idempotency_key"].type
    assert isinstance(attempt_key_type, String)
    assert attempt_key_type.length == 128
    attempt_hash_type = attempt_table.columns["request_hash"].type
    assert isinstance(attempt_hash_type, String)
    assert attempt_hash_type.length == 64
    assert attempt_table.columns["request_hash"].nullable

    # Specific assertions for session_command_idempotency
    idemp_table = Base.metadata.tables["session_command_idempotency"]
    idemp_key_type = idemp_table.columns["idempotency_key"].type
    assert isinstance(idemp_key_type, String)
    assert idemp_key_type.length == 255
    req_hash_type = idemp_table.columns["request_hash"].type
    assert isinstance(req_hash_type, String)
    assert req_hash_type.length == 64
    assert not idemp_table.columns["actor_id"].nullable
    assert not idemp_table.columns["session_id"].nullable

    idemp_fks = {fk.column.table.name: fk for fk in idemp_table.foreign_keys}
    assert "users" in idemp_fks
    assert idemp_fks["users"].ondelete == "CASCADE"
    assert "practice_sessions" in idemp_fks
    assert idemp_fks["practice_sessions"].ondelete == "CASCADE"

    uq_idemp = [
        c
        for c in idemp_table.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_session_command_idempotency"
    ]
    assert len(uq_idemp) == 1
    assert set(uq_idemp[0].columns.keys()) == {
        "session_id",
        "actor_id",
        "operation",
        "idempotency_key",
    }

    # Specific assertions for session_manifest_documents
    doc_table = Base.metadata.tables["session_manifest_documents"]
    assert "created_at" in doc_table.columns
    assert not doc_table.columns["created_at"].nullable
    doc_fks = {fk.column.table.name: fk for fk in doc_table.foreign_keys}
    assert "session_manifests" in doc_fks
    assert doc_fks["session_manifests"].ondelete == "CASCADE"
    assert "asset_versions" in doc_fks

    uq_doc = [
        c
        for c in doc_table.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_manifest_document_version"
    ]
    assert len(uq_doc) == 1
    assert set(uq_doc[0].columns.keys()) == {"manifest_id", "document_version_id"}

    # Specific assertions for analysis_jobs
    job_table = Base.metadata.tables["analysis_jobs"]
    assert not job_table.columns["practice_session_id"].nullable
    job_type_type = job_table.columns["job_type"].type
    assert isinstance(job_type_type, String)
    assert job_type_type.length == 50
    assert not job_table.columns["job_type"].nullable

    # Specific assertions for analysis_stages
    stage_table = Base.metadata.tables["analysis_stages"]
    assert not stage_table.columns["attempt_id"].nullable
    uq_stage = [
        c
        for c in stage_table.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_analysis_stage_attempt_stage"
    ]
    assert len(uq_stage) == 1
    assert set(uq_stage[0].columns.keys()) == {"attempt_id", "stage"}

    # Specific assertions for speaker_mappings
    speaker_table = Base.metadata.tables["speaker_mappings"]
    assert not speaker_table.columns["attempt_id"].nullable
    assert speaker_table.columns["member_id"].nullable
    uq_speaker = [
        c
        for c in speaker_table.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_speaker_mapping_attempt_label"
    ]
    assert len(uq_speaker) == 1
    assert set(uq_speaker[0].columns.keys()) == {"attempt_id", "speaker_label"}

    engine.dispose()
