import json
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.application.ports.ai_queue import AIQueueTemporaryFailure
from app.infrastructure.queues.celery_ai_job_queue import CeleryAIJobQueue


def delivery(job_id: str) -> str:
    return json.dumps({"headers": {"id": job_id}, "body": "synthetic-body"})


@pytest.mark.anyio
async def test_cancellation_removes_exact_queue_and_unacked_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeRedis()
    target, other = delivery("target"), delivery("other")
    redis.rpush("ai_jobs", target, other)
    redis.rpush("ai_jobs\x06\x163", target, other)
    redis.hset("unacked", "target-tag", json.dumps([json.loads(target), "", "ai_jobs"]))
    redis.hset("unacked", "other-tag", json.dumps([json.loads(other), "", "ai_jobs"]))
    redis.zadd("unacked_index", {"target-tag": 1, "other-tag": 2})
    monkeypatch.setattr(
        "app.infrastructure.queues.celery_ai_job_queue.Redis.from_url", lambda _: redis
    )
    celery = MagicMock()
    inspect = celery.control.inspect.return_value
    for snapshot in (inspect.active, inspect.reserved, inspect.scheduled):
        snapshot.return_value = {"worker": []}
    assert await CeleryAIJobQueue(celery_app=celery).cancel_jobs(["target"]) == 3
    assert redis.lrange("ai_jobs", 0, -1) == [other.encode()]
    assert redis.hget("unacked", "target-tag") is None
    assert redis.hget("unacked", "other-tag") is not None
    assert redis.zscore("unacked_index", "other-tag") == 2
    celery.control.revoke.assert_called_once_with(["target"], terminate=False)


@pytest.mark.anyio
@pytest.mark.parametrize("snapshot", [None, {"worker": [{"id": "target"}]}])
async def test_unavailable_or_active_worker_prevents_purge(
    monkeypatch: pytest.MonkeyPatch, snapshot: object
) -> None:
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(
        "app.infrastructure.queues.celery_ai_job_queue.Redis.from_url", lambda _: redis
    )
    celery = MagicMock()
    celery.control.inspect.return_value.active.return_value = snapshot
    with pytest.raises(AIQueueTemporaryFailure):
        await CeleryAIJobQueue(celery_app=celery).cancel_jobs(["target"])
