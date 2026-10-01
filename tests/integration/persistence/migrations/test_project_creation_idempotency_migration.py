from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect

from tests.integration.persistence.migrations.test_migrations import migration_configuration


def test_project_creation_idempotency_migration_round_trip(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'project-creation.db'}"
    config = migration_configuration(url)
    command.upgrade(config, "f7a8b9c0d1e2")
    engine = create_engine(url)
    try:
        assert "project_creation_idempotency" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert set(
            inspector.get_pk_constraint("project_creation_idempotency")["constrained_columns"]
        ) == {"user_id", "team_id", "operation", "key"}
        foreign_keys = inspector.get_foreign_keys("project_creation_idempotency")
        assert {key["referred_table"] for key in foreign_keys} == {"users", "teams"}
        assert all(key["options"]["ondelete"] == "CASCADE" for key in foreign_keys)
        command.downgrade(config, "f7a8b9c0d1e2")
        assert "project_creation_idempotency" not in inspect(engine).get_table_names()
        assert "projects" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "project_creation_idempotency" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
