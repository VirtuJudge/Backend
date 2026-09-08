import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.main import create_app
from app.settings import Settings


@pytest.mark.anyio
async def test_scheduler_loop_resilience_and_shutdown_cancellation(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        asset_cleanup_enabled=True,
        asset_cleanup_interval_seconds=0.01,
        asset_cleanup_batch_size=5,
    )

    app = create_app(settings)

    call_count = 0
    success_event = asyncio.Event()

    class FakeCleanupStore:
        async def cleanup_abandoned_uploads(self, **kwargs: Any) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated transient cleanup failure")
            success_event.set()
            return 1

    app.state.asset_store_factory = lambda session: FakeCleanupStore()

    # Enter lifespan to start the cleanup task
    async with app.router.lifespan_context(app):
        # Must recover after transient error and tick successfully
        await asyncio.wait_for(success_event.wait(), timeout=1.0)
        assert call_count >= 2

    calls_at_exit = call_count
    await asyncio.sleep(0.03)
    # Task was cancelled on shutdown; no further calls
    assert call_count == calls_at_exit

    # Exceptional lifespan exit must also clean resources (task cancelled, engine disposed)
    with pytest.raises(RuntimeError, match="Lifespan crash"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("Lifespan crash")
