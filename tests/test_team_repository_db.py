from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations import (
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository

NOW = datetime.now(UTC)


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "team_repo_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.anyio
async def test_list_members_retrieves_user_display_name(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_1 = UserModel(
            id=uuid4(),
            issuer="https://supabase.co/auth",
            subject="sub-1",
            email="ahmed@example.com",
            display_name="Ahmed El-Sherbiny",
            created_at=NOW,
        )
        user_2 = UserModel(
            id=uuid4(),
            issuer="https://supabase.co/auth",
            subject="sub-2",
            email="sarah@example.com",
            display_name="Sarah Chen",
            created_at=NOW,
        )
        team = TeamModel(id=uuid4(), name="Alpha Team", created_at=NOW, version=1)
        session.add_all([user_1, user_2, team])
        await session.flush()

        member_1 = TeamMemberModel(
            id=uuid4(),
            team_id=team.id,
            user_id=user_1.id,
            role="owner",
            joined_at=NOW,
        )
        member_2 = TeamMemberModel(
            id=uuid4(),
            team_id=team.id,
            user_id=user_2.id,
            role="member",
            joined_at=NOW,
        )
        session.add_all([member_1, member_2])
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyTeamRepository(session)
        members, next_cursor = await repo.list_members(team.id)

        assert len(members) == 2
        names = {m.display_name for m in members}
        assert "Ahmed El-Sherbiny" in names
        assert "Sarah Chen" in names

        # Also test get_membership
        membership = await repo.get_membership(team.id, user_1.id)
        assert membership is not None
        assert membership.display_name == "Ahmed El-Sherbiny"
        assert membership.role == "owner"
