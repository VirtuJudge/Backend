import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.services.user_service import UserService
from app.domain.user import User
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository


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
                found_by_id = await repository.get_by_id(user.id)
                assert found_by_id == found
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_existing_identity_refreshes_profile_claims(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine)
            issuer, subject = "https://identity.example", "synthetic-subject"

            async def resolve(email: str | None, name: str | None) -> User:
                async with sessions() as session:
                    user = await UserService(SqlAlchemyUserRepository(session)).get_or_create_user(
                        issuer, subject, email, name
                    )
                    await session.commit()
                    return user

            first = await resolve("old@example.com", "Old Name")
            refreshed = await resolve(" New@Example.com ", "New Name")
            kept = await resolve(None, "  ")

            assert refreshed.id == first.id
            assert (refreshed.email, refreshed.display_name) == ("new@example.com", "New Name")
            assert kept == refreshed
            async with sessions() as session:
                stored = await SqlAlchemyUserRepository(session).get_by_identity(issuer, subject)
            assert stored == refreshed
        finally:
            await engine.dispose()

    asyncio.run(exercise())
