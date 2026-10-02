from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, select

from tests.integration.persistence.migrations.test_migrations import migration_configuration
from tests.support.constants import NOW


def test_scope_migration_invalidates_keys_and_preserves_invitation(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'resend-migration.db'}"
    config = migration_configuration(url)
    command.upgrade(config, "1b2c3d4e5f6a")
    engine = create_engine(url)
    metadata = MetaData()
    teams = Table(
        "teams", metadata, Column("id", String(32), primary_key=True), autoload_with=engine
    )
    users = Table(
        "users", metadata, Column("id", String(32), primary_key=True), autoload_with=engine
    )
    invitations = Table(
        "team_invitations",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("team_id", String(32)),
        autoload_with=engine,
    )
    legacy_keys = Table(
        "invitation_resend_idempotency",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("invitation_id", String(32)),
        autoload_with=engine,
    )
    team_id, invitation_id, actor_id = (uuid4().hex for _ in range(3))
    with engine.begin() as connection:
        connection.execute(
            teams.insert().values(
                id=team_id,
                name="Team",
                created_at=NOW,
                version=1,
            )
        )
        connection.execute(
            users.insert().values(
                id=actor_id,
                email="owner@example.com",
                issuer="test",
                subject="owner",
                created_at=NOW,
            )
        )
        connection.execute(
            invitations.insert().values(
                id=invitation_id,
                team_id=team_id,
                email="invitee@example.com",
                token_hash="original",
                role="member",
                status="pending",
                delivery_status="queued",
                delivery_attempts=1,
                created_at=NOW,
                expires_at=NOW + timedelta(days=1),
                idempotency_key="create",
                version=2,
            )
        )
        connection.execute(
            legacy_keys.insert().values(
                id=uuid4().hex,
                invitation_id=invitation_id,
                key="legacy",
                created_at=NOW,
            )
        )
        before = dict(connection.execute(select(invitations)).mappings().one())

    command.upgrade(config, "head")
    scoped_keys = Table(
        "invitation_resend_idempotency",
        MetaData(),
        Column("id", String(32), primary_key=True),
        Column("invitation_id", String(32)),
        autoload_with=engine,
    )
    with engine.begin() as connection:
        assert connection.execute(select(scoped_keys)).first() is None
        assert dict(connection.execute(select(invitations)).mappings().one()) == before
        for operation in ("resend_invitation", "other_operation"):
            connection.execute(
                scoped_keys.insert().values(
                    id=uuid4().hex,
                    actor_id=actor_id,
                    team_id=team_id,
                    invitation_id=invitation_id,
                    operation=operation,
                    key="same-key",
                    request_hash="hash",
                    created_at=NOW,
                )
            )

    command.downgrade(config, "1b2c3d4e5f6a")
    restored = Table(
        "invitation_resend_idempotency",
        MetaData(),
        Column("id", String(32), primary_key=True),
        Column("invitation_id", String(32)),
        autoload_with=engine,
    )
    with engine.connect() as connection:
        assert connection.execute(select(restored)).first() is None
        assert dict(connection.execute(select(invitations)).mappings().one()) == before
    assert inspect(engine).get_unique_constraints("invitation_resend_idempotency")[0][
        "column_names"
    ] == ["key"]
    command.upgrade(config, "head")
    engine.dispose()
