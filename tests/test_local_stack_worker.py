import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from redis.exceptions import ConnectionError

from local_stack.config import HEARTBEAT_KEY, HEARTBEAT_TTL_SECONDS, QUEUE_KEY, RESULT_KEY_PREFIX
from local_stack.health import main as health_main
from local_stack.loader import AIComponents
from local_stack.worker import LocalStackWorker


class DiagnosticMessage(BaseModel):
    job_id: str


class DiagnosticUpdate(BaseModel):
    status: str
    payload: dict[str, Any]


def test_worker_consumes_job_and_publishes_correlated_expiring_result() -> None:
    queue = AsyncMock()
    queue.blpop.return_value = (QUEUE_KEY, '{"job_id":"synthetic-job"}')
    process = AsyncMock(return_value=DiagnosticUpdate(status="completed", payload={"ok": True}))
    worker = LocalStackWorker(queue, AIComponents(DiagnosticMessage, process, object))

    assert asyncio.run(worker.run_once()) == "synthetic-job"

    assert queue.set.call_args_list[0].args[0] == HEARTBEAT_KEY
    assert queue.set.call_args_list[0].kwargs["ex"] == HEARTBEAT_TTL_SECONDS
    result_call = queue.set.call_args_list[1]
    assert result_call.args[0] == RESULT_KEY_PREFIX + "synthetic-job"
    assert json.loads(result_call.args[1]) == {"status": "completed", "payload": {"ok": True}}
    assert result_call.kwargs["ex"] > 0


def test_malformed_message_does_not_prevent_next_job(caplog: pytest.LogCaptureFixture) -> None:
    queue = AsyncMock()
    queue.blpop.side_effect = [
        (QUEUE_KEY, '{"private":"do-not-log"}'),
        (QUEUE_KEY, '{"job_id":"next-job"}'),
    ]
    process = AsyncMock(return_value=DiagnosticUpdate(status="completed", payload={}))
    worker = LocalStackWorker(queue, AIComponents(DiagnosticMessage, process, object))

    async def consume() -> None:
        assert await worker.run_once() is None
        assert await worker.run_once() == "next-job"

    asyncio.run(consume())
    assert "do-not-log" not in caplog.text
    process.assert_awaited_once()


def test_failed_job_result_does_not_expose_provider_error() -> None:
    queue = AsyncMock()
    process = AsyncMock(
        return_value=DiagnosticUpdate(status="failed", payload={"message": "private-provider-body"})
    )
    worker = LocalStackWorker(queue, AIComponents(DiagnosticMessage, process, object))
    assert asyncio.run(worker.process_raw_message('{"job_id":"failed-job"}')) == "failed-job"
    assert json.loads(queue.set.call_args.args[1]) == {
        "status": "failed",
        "payload": {"message": "Diagnostic analysis failed"},
    }


@pytest.mark.parametrize("age, expected", [(0, 0), (30, 1), (-30, 1), (None, 1)])
def test_health_cli_rejects_missing_stale_or_future_heartbeat(
    age: int | None, expected: int
) -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = None if age is None else str(time.time() - age)
    with patch("local_stack.health.Redis.from_url", return_value=client):
        assert health_main() == expected


def test_health_cli_fails_when_redis_unavailable() -> None:
    with patch("local_stack.health.Redis.from_url", side_effect=ConnectionError):
        assert health_main() == 1
