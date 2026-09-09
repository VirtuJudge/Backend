import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.user import User
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository


def test_existing_identity_survives_a_new_session(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine)
            user = User(
                uuid4(),
                None,
                "https://identity.example",
                "synthetic-subject",
                None,
                datetime.now(UTC),
            )
            async with sessions() as session:
                await SqlAlchemyUserRepository(session).create(user)
                await session.commit()
            async with sessions() as session:
                repository = SqlAlchemyUserRepository(session)
                found = await repository.get_by_identity(user.issuer, user.subject)
                assert found is not None
                assert (found.id, found.issuer, found.subject, found.email) == (
                    user.id,
                    user.issuer,
                    user.subject,
                    None,
                )
                assert await repository.get_by_identity(user.issuer, "another-subject") is None
        finally:
            await engine.dispose()

    asyncio.run(exercise())
