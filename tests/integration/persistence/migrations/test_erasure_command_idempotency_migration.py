from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, insert, inspect, select

from app.infrastructure.persistence.configurations import (
    ErasureCommandIdempotencyModel,
    ErasureRequestModel,
)
from tests.integration.persistence.migrations.test_migrations import migration_configuration


def test_erasure_command_migration_backfills_user_keys_and_round_trips(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'erasure-keys.db'}"
    config = migration_configuration(url)
    command.upgrade(config, "e6f7a8b9c0d1")
    engine = create_engine(url)
    actor, request_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    digest = sha256(b"Synthetic project").hexdigest()
    with engine.begin() as connection:
        for requested_by, target_id in ((actor, request_id), (None, uuid4())):
            connection.execute(
                insert(ErasureRequestModel).values(
                    id=target_id,
                    team_id=uuid4(),
                    project_id=uuid4(),
                    scope="project",
                    scope_id=uuid4(),
                    requested_by=requested_by,
                    origin="user_request" if requested_by else "retention",
                    idempotency_key="original",
                    request_hash=digest,
                    status="completed",
                    requested_at=now,
                    deadline_at=now + timedelta(hours=24),
                    next_attempt_at=now,
                    inventory={},
                )
            )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        records = (
            connection.execute(select(ErasureCommandIdempotencyModel.__table__)).mappings().all()
        )
        assert len(records) == 1
        assert records[0]["actor_id"] == actor
        assert records[0]["request_id"] == request_id
        assert records[0]["request_hash"] == digest
        assert records[0]["idempotency_key"] == "original"
    command.downgrade(config, "e6f7a8b9c0d1")
    assert "erasure_command_idempotency" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert len(connection.execute(select(ErasureRequestModel.__table__)).all()) == 2
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert len(connection.execute(select(ErasureCommandIdempotencyModel.__table__)).all()) == 1
    engine.dispose()
