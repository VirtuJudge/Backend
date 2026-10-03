import importlib
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects.postgresql.base import PGInspector

from app.domain.team_invitation import DeliveryStatus
from tests.integration.persistence.migrations.test_migrations import migration_configuration

MIGRATION = importlib.import_module(
    "migrations.versions.3d4e5f6a7b8c_neutral_invitation_delivery_status"
)


def test_sqlite_delivery_status_upgrade_and_rollback_preserve_records(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'delivery-status.db'}"
    config = migration_configuration(url)
    command.upgrade(config, "2c3d4e5f6a7b")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO teams (id, name, created_at, version) "
                "VALUES (:id, 'Team', CURRENT_TIMESTAMP, 1)"
            ),
            {"id": uuid4().hex},
        )
        for status in ("queued", "accepted_by_gmail", "failed"):
            connection.execute(
                sa.text(
                    "INSERT INTO team_invitations "
                    "(id, team_id, email, token_hash, role, status, delivery_status, "
                    "delivery_attempts, created_at, expires_at, idempotency_key, version) "
                    "SELECT :id, id, 'invitee@example.com', :token, 'member', 'pending', "
                    ":status, 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :key, 3 FROM teams"
                ),
                {"id": uuid4().hex, "token": uuid4().hex, "status": status, "key": uuid4().hex},
            )
        before = [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM team_invitations ORDER BY id")
            ).mappings()
        ]

    command.upgrade(config, "head")
    with engine.connect() as connection:
        after = [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM team_invitations ORDER BY id")
            ).mappings()
        ]
    expected = [
        {**row, "delivery_status": "accepted_by_provider"}
        if row["delivery_status"] == "accepted_by_gmail"
        else row
        for row in before
    ]
    assert after == expected
    command.downgrade(config, "2c3d4e5f6a7b")
    with engine.connect() as connection:
        restored = [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM team_invitations ORDER BY id")
            ).mappings()
        ]
    assert restored == before
    command.upgrade(config, "head")
    engine.dispose()


def test_postgres_delivery_status_migrates_enum_records_and_new_writes() -> None:
    url = os.environ.get("PROJECT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PROJECT_TEST_DATABASE_URL not configured")
    sync_url = sa.make_url(url).set(drivername="postgresql+psycopg")
    engine = sa.create_engine(sync_url)
    schema = f"delivery_status_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(sa.schema.CreateSchema(schema))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            legacy = sa.Table(
                "team_invitations",
                sa.MetaData(),
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column(
                    "delivery_status",
                    sa.Enum("queued", "accepted_by_gmail", "failed", name="deliverystatus"),
                    nullable=False,
                ),
            )
            legacy.create(connection)
            connection.execute(
                legacy.insert(),
                [
                    {"id": index, "delivery_status": status}
                    for index, status in enumerate(("queued", "accepted_by_gmail", "failed"))
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
            current = sa.Table(
                "team_invitations",
                sa.MetaData(),
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column(
                    "delivery_status",
                    sa.Enum(
                        DeliveryStatus,
                        values_callable=lambda values: [v.value for v in values],
                        name="deliverystatus",
                    ),
                ),
            )
            assert list(
                connection.scalars(sa.select(current.c.delivery_status).order_by(current.c.id))
            ) == [DeliveryStatus.QUEUED, DeliveryStatus.ACCEPTED, DeliveryStatus.FAILED]
            connection.execute(
                current.insert().values(id=3, delivery_status=DeliveryStatus.ACCEPTED)
            )
            labels = cast(PGInspector, sa.inspect(connection)).get_enums(schema=schema)[0]["labels"]
            assert labels == ["queued", "accepted_by_provider", "failed"]
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.downgrade()
            assert list(
                connection.scalars(sa.select(legacy.c.delivery_status).order_by(legacy.c.id))
            ) == ["queued", "accepted_by_gmail", "failed", "accepted_by_gmail"]
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
    finally:
        with engine.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        engine.dispose()
