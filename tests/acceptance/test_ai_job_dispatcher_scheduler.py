import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from app.application.ai_jobs import RedispatchResult
from app.main import create_app
from app.settings import Settings


@pytest.mark.anyio
async def test_ai_job_dispatcher_settings_configuration(tmp_path: Path) -> None:
    db_path = tmp_path / "config_test.db"

    # Default settings in test environment
    default_settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )
    assert default_settings.ai_job_dispatcher_enabled is None
    assert default_settings.ai_dispatcher_enabled is None
    assert default_settings.ai_job_dispatcher_interval_seconds == 10.0
    assert default_settings.ai_dispatcher_interval_seconds == 10.0
    assert default_settings.ai_job_dispatcher_batch_size == 50
    assert default_settings.ai_dispatcher_batch_size == 50
    assert default_settings.ai_job_dispatcher_base_backoff_seconds == 30.0
    assert default_settings.ai_dispatcher_base_backoff_seconds == 30.0
    assert default_settings.ai_job_dispatcher_max_backoff_seconds == 3600.0
    assert default_settings.ai_dispatcher_max_backoff_seconds == 3600.0
    assert default_settings.ai_job_dispatcher_backoff_factor == 2.0
    assert default_settings.ai_dispatcher_backoff_factor == 2.0

    # Custom settings using primary names
    custom_settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=True,
        ai_job_dispatcher_interval_seconds=0.05,
        ai_job_dispatcher_batch_size=25,
        ai_job_dispatcher_base_backoff_seconds=15.0,
        ai_job_dispatcher_max_backoff_seconds=120.0,
        ai_job_dispatcher_backoff_factor=3.0,
    )
    assert custom_settings.ai_job_dispatcher_enabled is True
    assert custom_settings.ai_dispatcher_enabled is True
    assert custom_settings.ai_job_dispatcher_interval_seconds == 0.05
    assert custom_settings.ai_dispatcher_interval_seconds == 0.05
    assert custom_settings.ai_job_dispatcher_batch_size == 25
    assert custom_settings.ai_dispatcher_batch_size == 25
    assert custom_settings.ai_job_dispatcher_base_backoff_seconds == 15.0
    assert custom_settings.ai_dispatcher_base_backoff_seconds == 15.0
    assert custom_settings.ai_job_dispatcher_max_backoff_seconds == 120.0
    assert custom_settings.ai_dispatcher_max_backoff_seconds == 120.0
    assert custom_settings.ai_job_dispatcher_backoff_factor == 3.0
    assert custom_settings.ai_dispatcher_backoff_factor == 3.0

    # Alias settings using ai_dispatcher_* names
    alias_settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_dispatcher_enabled=True,
        ai_dispatcher_interval_seconds=0.02,
        ai_dispatcher_batch_size=10,
        ai_dispatcher_base_backoff_seconds=5.0,
        ai_dispatcher_max_backoff_seconds=60.0,
        ai_dispatcher_backoff_factor=1.5,
    )
    assert alias_settings.ai_job_dispatcher_enabled is True
    assert alias_settings.ai_dispatcher_enabled is True
    assert alias_settings.ai_job_dispatcher_interval_seconds == 0.02
    assert alias_settings.ai_dispatcher_interval_seconds == 0.02
    assert alias_settings.ai_job_dispatcher_batch_size == 10
    assert alias_settings.ai_dispatcher_batch_size == 10
    assert alias_settings.ai_job_dispatcher_base_backoff_seconds == 5.0
    assert alias_settings.ai_dispatcher_base_backoff_seconds == 5.0
    assert alias_settings.ai_job_dispatcher_max_backoff_seconds == 60.0
    assert alias_settings.ai_dispatcher_max_backoff_seconds == 60.0
    assert alias_settings.ai_job_dispatcher_backoff_factor == 1.5
    assert alias_settings.ai_dispatcher_backoff_factor == 1.5

    # In default test mode, background loop task is not launched
    app = create_app(default_settings)
    async with app.router.lifespan_context(app):
        assert app.state.ai_job_dispatcher_task is None


@pytest.mark.anyio
async def test_ai_job_dispatcher_repeated_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "repeated_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=True,
        ai_job_dispatcher_interval_seconds=0.01,
        ai_job_dispatcher_batch_size=5,
    )

    app = create_app(settings)

    call_count = 0
    at_least_three_calls = asyncio.Event()

    class FakeAIJobs:
        async def redispatch_pending(self, **kwargs: Any) -> RedispatchResult:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                at_least_three_calls.set()
            return RedispatchResult(processed=1, queued=1, failed=0)

    app.state.ai_jobs_factory = lambda uow, queue: FakeAIJobs()

    async with app.router.lifespan_context(app):
        assert app.state.ai_job_dispatcher_task is not None
        await asyncio.wait_for(at_least_three_calls.wait(), timeout=1.0)
        assert call_count >= 3


@pytest.mark.anyio
async def test_ai_job_dispatcher_failure_recovery_and_safe_logging(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "failure_recovery_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=True,
        ai_job_dispatcher_interval_seconds=0.01,
        ai_job_dispatcher_batch_size=5,
    )

    app = create_app(settings)

    call_count = 0
    success_event = asyncio.Event()

    class FlakyAIJobs:
        async def redispatch_pending(self, **kwargs: Any) -> RedispatchResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Database connection timeout with payload=SECRET_TOKEN_123")
            success_event.set()
            return RedispatchResult(processed=1, queued=1, failed=0)

    app.state.ai_jobs_factory = lambda uow, queue: FlakyAIJobs()

    with caplog.at_level(logging.WARNING):
        async with app.router.lifespan_context(app):
            # Must survive individual iteration failure and execute second iteration
            await asyncio.wait_for(success_event.wait(), timeout=1.0)
            assert call_count >= 2

    # Log only safe error category (RuntimeError), never secrets or payload bodies
    assert "Periodic AI job redispatch encountered error: RuntimeError" in caplog.text
    assert "SECRET_TOKEN_123" not in caplog.text


@pytest.mark.anyio
async def test_ai_job_dispatcher_shutdown_cancellation_and_clean_exit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "shutdown_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=True,
        ai_job_dispatcher_interval_seconds=0.01,
        ai_job_dispatcher_batch_size=5,
    )

    app = create_app(settings)

    call_count = 0
    first_call = asyncio.Event()

    class FakeAIJobs:
        async def redispatch_pending(self, **kwargs: Any) -> RedispatchResult:
            nonlocal call_count
            call_count += 1
            first_call.set()
            return RedispatchResult(processed=0, queued=0, failed=0)

    app.state.ai_jobs_factory = lambda uow, queue: FakeAIJobs()

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(first_call.wait(), timeout=1.0)
        assert call_count >= 1

    calls_at_exit = call_count
    await asyncio.sleep(0.03)
    # Background loop cancelled and stopped cleanly; no further calls
    assert call_count == calls_at_exit

    # Exceptional lifespan exit must also clean up cleanly
    with pytest.raises(RuntimeError, match="Lifespan crash"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("Lifespan crash")


@pytest.mark.anyio
async def test_ai_job_dispatcher_prevents_overlapping_iterations(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "overlap_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=False,
    )

    app = create_app(settings)

    iteration_entered = asyncio.Event()
    release_iteration = asyncio.Event()
    concurrent_calls = 0
    max_concurrent = 0

    class SlowAIJobs:
        async def redispatch_pending(self, **kwargs: Any) -> RedispatchResult:
            nonlocal concurrent_calls, max_concurrent
            concurrent_calls += 1
            max_concurrent = max(max_concurrent, concurrent_calls)
            iteration_entered.set()
            await release_iteration.wait()
            concurrent_calls -= 1
            return RedispatchResult(processed=1, queued=1, failed=0)

    app.state.ai_jobs_factory = lambda uow, queue: SlowAIJobs()

    # Start first iteration in background task
    task1 = asyncio.create_task(app.state.run_ai_job_dispatcher_iteration())
    await asyncio.wait_for(iteration_entered.wait(), timeout=1.0)

    # Second iteration attempted while first is still running
    result2 = await app.state.run_ai_job_dispatcher_iteration()
    # Lock prevented overlapping run; returned empty result immediately
    assert result2 == RedispatchResult(0, 0, 0)
    assert max_concurrent == 1

    # Release first iteration
    release_iteration.set()
    result1 = await task1
    assert result1 == RedispatchResult(1, 1, 0)
    assert max_concurrent == 1


@pytest.mark.anyio
async def test_ai_job_dispatcher_deterministic_run_once(tmp_path: Path) -> None:
    db_path = tmp_path / "deterministic_test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        ai_job_dispatcher_enabled=False,
    )

    app = create_app(settings)

    call_count = 0

    class CountingAIJobs:
        async def redispatch_pending(self, **kwargs: Any) -> RedispatchResult:
            nonlocal call_count
            call_count += 1
            return RedispatchResult(processed=2, queued=2, failed=0)

    app.state.ai_jobs_factory = lambda uow, queue: CountingAIJobs()

    # Deterministic on-demand execution
    result = await app.state.run_ai_job_dispatcher_iteration()
    assert call_count == 1
    assert result == RedispatchResult(2, 2, 0)
    assert int(result) == 2
