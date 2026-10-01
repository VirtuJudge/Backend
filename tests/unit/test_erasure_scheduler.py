import asyncio
from unittest.mock import AsyncMock

import pytest

from app.main import create_app
from app.settings import Settings


@pytest.mark.anyio
async def test_erasure_scheduler_recovers_failure_and_stops_on_shutdown() -> None:
    app = create_app(
        Settings(
            app_env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            erasure_enabled=True,
            erasure_interval_seconds=0.001,
        )
    )
    recovered = asyncio.Event()
    calls = 0

    async def iteration() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Synthetic dependency outage")
        recovered.set()
        return 0

    app.state.run_erasure_iteration = iteration
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(recovered.wait(), timeout=2)
        assert not app.state.erasure_task.done()
    assert app.state.erasure_task.done()
    assert calls >= 2


@pytest.mark.anyio
async def test_erasure_scheduler_is_disabled_by_default_in_tests() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    app.state.run_erasure_iteration = AsyncMock()
    async with app.router.lifespan_context(app):
        assert app.state.erasure_task is None
    app.state.run_erasure_iteration.assert_not_awaited()
