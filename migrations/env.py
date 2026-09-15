from logging.config import fileConfig

from alembic import context
from sqlalchemy import Column, MetaData, String, Table, inspect, pool, select
from sqlalchemy.engine import Connection, engine_from_config, make_url

import app.infrastructure.persistence.configurations  # noqa: F401
from app.infrastructure.database import metadata
from app.settings import Settings

config = getattr(context, "config", None)

if config is not None and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata
VERSION_TABLE = "backend_alembic_version"
LEGACY_BACKEND_REVISION = "c210932aac79"
LEGACY_SCHEMA_COLUMNS = {
    "users": {"display_name"},
    "teams": {"version"},
    "team_members": set(),
    "team_creation_idempotency": set(),
    "projects": {"version"},
    "project_erasure_requests": set(),
    "assets": {"retention_expires_at"},
    "asset_versions": {"upload_expires_at", "cleanup_next_attempt_at"},
    "asset_upload_idempotency": set(),
    "team_invitations": {"version"},
    "invitation_resend_idempotency": set(),
}
LEGACY_SCHEMA_ABSENT_TABLES = {"invitation_idempotency_keys", "practice_sessions"}


def database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url") if config is not None else None
    return configured_url or Settings().database_url


def build_migration_url(raw_url: str) -> str:
    url = make_url(raw_url)
    driver = {
        "postgresql+asyncpg": "postgresql+psycopg",
        "postgresql": "postgresql+psycopg",
        "postgres": "postgresql+psycopg",
        "sqlite+aiosqlite": "sqlite+pysqlite",
    }.get(url.drivername, url.drivername)
    query = dict(url.query)
    if ("psycopg" in driver or driver == "postgresql") and "ssl" in query:
        ssl_val = query.pop("ssl")
        if "sslmode" not in query:
            query["sslmode"] = ssl_val
    return url.set(drivername=driver, query=query).render_as_string(hide_password=False)


def migration_url() -> str:
    return build_migration_url(database_url())


REVISION_ORDER = [
    "287738efa54f",
    "8005859f45dd",
    "9d6a1b2c3e4f",
    "a1b2c3d4e5f6",
    "b2c3d4e5f6a7",
    "c4d5e6f7a8b9",
    "d5e6f7a8b9c0",
    "edf3ff11ceff",
    "a58101e6379c",
    "c06eb23ba31c",
    "b5af41259a43",
    "0989108a15b1",
    "c210932aac79",
    "d0bab208d7c4",
    "13b7d8c79976",
    "ef143c2a901b",
    "40d664ee8b4d",
    "f31a1f55d085",
    "3f9a7c2d1e6b",
    "7b4e1a6d2c8f",
    "8c5d2e3f4a1b",
    "a4f6c8e1d2b3",
]


def stamp_legacy_backend_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    detected_rev: str | None = None
    if "ai_jobs" in tables:
        ai_jobs_cols = {c["name"] for c in inspector.get_columns("ai_jobs")}
        if "answer_id" in ai_jobs_cols:
            detected_rev = "7b4e1a6d2c8f"
        elif "qa_rounds" in tables:
            detected_rev = "3f9a7c2d1e6b"
        elif "payload" in ai_jobs_cols:
            detected_rev = "f31a1f55d085"
        else:
            detected_rev = "40d664ee8b4d"
    elif "analysis_jobs" in tables:
        detected_rev = "ef143c2a901b"
    elif "practice_sessions" in tables:
        if "analysis_attempts" in tables:
            attempt_cols = {c["name"] for c in inspector.get_columns("analysis_attempts")}
            detected_rev = "13b7d8c79976" if "failed_at" in attempt_cols else "d0bab208d7c4"
        else:
            detected_rev = "d0bab208d7c4"
    elif LEGACY_SCHEMA_COLUMNS.keys() <= tables and not (LEGACY_SCHEMA_ABSENT_TABLES & tables):
        columns_match = True
        for table_name, required_columns in LEGACY_SCHEMA_COLUMNS.items():
            cols = {c["name"] for c in inspector.get_columns(table_name)}
            if not required_columns <= cols:
                columns_match = False
                break
        if columns_match:
            detected_rev = LEGACY_BACKEND_REVISION

    if detected_rev is None:
        return

    version_table = Table(
        VERSION_TABLE,
        MetaData(),
        Column("version_num", String(32), primary_key=True, nullable=False),
    )
    if VERSION_TABLE not in tables:
        version_table.create(connection)
        connection.execute(version_table.insert().values(version_num=detected_rev))
    else:
        current_rev = connection.execute(select(version_table.c.version_num)).scalar_one_or_none()
        if current_rev is None or (
            current_rev in REVISION_ORDER
            and detected_rev in REVISION_ORDER
            and REVISION_ORDER.index(detected_rev) > REVISION_ORDER.index(current_rev)
        ):
            connection.execute(version_table.update().values(version_num=detected_rev))


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        stamp_legacy_backend_schema(connection)
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {}) if config is not None else {}
    configuration["sqlalchemy.url"] = migration_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        run_sync_migrations(connection)


if config is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
