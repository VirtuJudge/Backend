from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.settings import Settings


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


def create_database_engine(settings: Settings) -> AsyncEngine:
    url = make_url(settings.database_url)
    async_driver = {
        "postgresql": "postgresql+asyncpg",
        "postgresql+psycopg2": "postgresql+asyncpg",
        "sqlite": "sqlite+aiosqlite",
    }.get(url.drivername)
    if async_driver is not None:
        url = url.set(drivername=async_driver)
    return create_async_engine(url, pool_pre_ping=True)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
