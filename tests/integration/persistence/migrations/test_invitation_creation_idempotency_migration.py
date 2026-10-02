from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from tests.integration.persistence.migrations.test_migrations import migration_configuration


def test_invitation_creation_migration_preserves_legacy_rows_and_guards_downgrade(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'invitation-creation.db'}"
    config = migration_configuration(url)
    command.upgrade(config, "0a1b2c3d4e5f")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO teams (id, name, created_at, version) "
                "VALUES ('t1', 'Team', '2026-10-02 10:00:00', 1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO team_invitations "
                "(id, team_id, email, token_hash, role, status, delivery_status, "
                "delivery_attempts, created_at, expires_at, idempotency_key, version) "
                "VALUES ('i1', 't1', 'invitee@example.com', 'legacy-token-hash', 'member', "
                "'pending', 'failed', 2, '2026-10-02 10:00:00', '2026-10-09 10:00:00', 'key', 3)"
            )
            before = connection.exec_driver_sql("SELECT * FROM team_invitations").one()
        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert set(
            inspector.get_pk_constraint("invitation_creation_idempotency")["constrained_columns"]
        ) == {"actor_id", "team_id", "operation", "key"}
        foreign_keys = inspector.get_foreign_keys("invitation_creation_idempotency")
        assert {key["referred_table"] for key in foreign_keys} == {
            "users",
            "teams",
            "team_invitations",
        }
        assert all(key["options"]["ondelete"] == "CASCADE" for key in foreign_keys)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("SELECT * FROM team_invitations").one() == before
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM invitation_creation_idempotency"
                ).scalar_one()
                == 0
            )
            connection.exec_driver_sql(
                "INSERT INTO team_invitations "
                "(id, team_id, email, token_hash, role, status, delivery_status, "
                "delivery_attempts, created_at, expires_at, idempotency_key, version) "
                "SELECT 'i2', team_id, email, 'second-token-hash', role, status, delivery_status, "
                "delivery_attempts, created_at, expires_at, idempotency_key, version "
                "FROM team_invitations WHERE id = 'i1'"
            )
        with pytest.raises(ValueError, match="scoped duplicates"):
            command.downgrade(config, "0a1b2c3d4e5f")
        assert "invitation_creation_idempotency" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM team_invitations WHERE id = 'i2'")
        command.downgrade(config, "0a1b2c3d4e5f")
        inspector = inspect(engine)
        assert "invitation_creation_idempotency" not in inspector.get_table_names()
        assert any(
            constraint["column_names"] == ["idempotency_key"]
            for constraint in inspector.get_unique_constraints("team_invitations")
        )
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT * FROM team_invitations").one() == before
        command.upgrade(config, "head")
    finally:
        engine.dispose()
