import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.user_service import UserService
from app.domain.user import User
from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations import UserModel
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


@pytest.fixture
async def postgres_users() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("PROJECT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PROJECT_TEST_DATABASE_URL not configured")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.anyio
async def test_postgres_simultaneous_first_requests_share_one_user(
    postgres_users: async_sessionmaker[AsyncSession],
) -> None:
    issuer, subject = "https://identity.example", f"race-{uuid4()}"
    ready = asyncio.Event()
    arrivals = 0

    class RacingRepository(SqlAlchemyUserRepository):
        async def get_by_identity(self, issuer: str, subject: str) -> User | None:
            nonlocal arrivals
            found = await super().get_by_identity(issuer, subject)
            if found is None:
                arrivals += 1
                if arrivals == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=10)
            return found

    async def first_request(email: str) -> User:
        async with postgres_users() as session:
            user = await UserService(RacingRepository(session)).get_or_create_user(
                issuer, subject, email, "Racer"
            )
            await session.commit()
            return user

    first, second = await asyncio.gather(
        first_request("racer@example.com"), first_request("racer@example.com")
    )

    assert first.id == second.id
    async with postgres_users() as session:
        rows = await session.scalars(
            select(UserModel.id).where(UserModel.issuer == issuer, UserModel.subject == subject)
        )
        assert list(rows) == [first.id]


@pytest.mark.anyio
async def test_postgres_create_still_raises_unrelated_integrity_errors(
    postgres_users: async_sessionmaker[AsyncSession],
) -> None:
    user = User(uuid4(), None, "https://identity.example", f"pk-{uuid4()}", None, datetime.now(UTC))
    async with postgres_users() as session:
        await SqlAlchemyUserRepository(session).create(user)
        await session.commit()

    clash = replace(user, subject=f"other-{uuid4()}")
    async with postgres_users() as session:
        with pytest.raises(IntegrityError):
            await SqlAlchemyUserRepository(session).create(clash)
