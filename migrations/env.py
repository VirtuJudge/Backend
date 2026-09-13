from logging.config import fileConfig

from alembic import context
from sqlalchemy import Column, MetaData, String, Table, inspect, pool
from sqlalchemy.engine import Connection, engine_from_config, make_url

import app.infrastructure.persistence.configurations  # noqa: F401
from app.infrastructure.database import metadata
from app.settings import Settings

config = context.config

if config.config_file_name is not None:
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
    configured_url = config.get_main_option("sqlalchemy.url")
    return configured_url or Settings().database_url


def migration_url() -> str:
    url = make_url(database_url())
    driver = {
        "postgresql+asyncpg": "postgresql+psycopg",
        "sqlite+aiosqlite": "sqlite+pysqlite",
    }.get(url.drivername, url.drivername)
    return url.set(drivername=driver).render_as_string(hide_password=False)


def stamp_legacy_backend_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if (
        VERSION_TABLE in tables
        or not LEGACY_SCHEMA_COLUMNS.keys() <= tables
        or LEGACY_SCHEMA_ABSENT_TABLES & tables
    ):
        return

    for table_name, required_columns in LEGACY_SCHEMA_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not required_columns <= columns:
            return

    version_table = Table(
        VERSION_TABLE,
        MetaData(),
        Column("version_num", String(32), primary_key=True, nullable=False),
    )
    version_table.create(connection)
    connection.execute(version_table.insert().values(version_num=LEGACY_BACKEND_REVISION))


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
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = migration_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        run_sync_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
