from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from tests.support.constants import NOW


@pytest.fixture
async def db_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def seed_db(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    async with db_session_factory() as session:
        user_id = uuid4()
        team_id = uuid4()
        project_id = uuid4()

        session.add(
            UserModel(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                issuer="https://auth.example",
                subject=f"sub-{user_id.hex[:6]}",
                created_at=NOW,
            )
        )
        session.add(TeamModel(id=team_id, name="Test Team", created_at=NOW))
        session.add(
            TeamMemberModel(
                team_id=team_id,
                user_id=user_id,
                role="member",
                joined_at=NOW,
            )
        )
        session.add(
            ProjectModel(
                id=project_id,
                team_id=team_id,
                name="Test Project",
                created_at=NOW,
                version=1,
            )
        )
        await session.commit()
        return user_id, team_id, project_id
