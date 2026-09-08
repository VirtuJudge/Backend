import json
from unittest.mock import patch

import pytest

from local_stack.smoke import check_ai_worker
from tests.support.smoke import FakeSyncRedis


def test_check_ai_worker_success() -> None:
    store: dict[str, str] = {}

    def handle_rpush(key: str, val: str) -> None:
        data = json.loads(val)
        job_id = data["job_id"]
        result = {
            "status": "completed",
            "payload": {
                "primary_questions": [
                    {"candidate_id": "1", "text": "Question 1?", "evidence_ids": ["ev1"]},
                    {"candidate_id": "2", "text": "Question 2?", "evidence_ids": ["ev2"]},
                    {"candidate_id": "3", "text": "Question 3?", "evidence_ids": ["ev3"]},
                ]
            },
        }
        result["trace_id"] = data["trace_id"]
        store[f"virtujudge:local:result:{job_id}"] = json.dumps(result)

    fake_redis = FakeSyncRedis(store=store, rpush_handler=handle_rpush)

    with patch("redis.from_url", return_value=fake_redis):
        check_ai_worker("redis://localhost:6379/0", timeout_seconds=2.0)

    assert any(k.startswith("virtujudge:local:result:") for k in fake_redis.deleted_keys)


def test_check_ai_worker_failure_terminal_status() -> None:
    store: dict[str, str] = {}

    def handle_rpush(key: str, val: str) -> None:
        data = json.loads(val)
        job_id = data["job_id"]
        result = {"status": "failed", "payload": {}}
        result["trace_id"] = data["trace_id"]
        store[f"virtujudge:local:result:{job_id}"] = json.dumps(result)

    fake_redis = FakeSyncRedis(store=store, rpush_handler=handle_rpush)

    with (
        patch("redis.from_url", return_value=fake_redis),
        pytest.raises(RuntimeError, match="terminal status 'failed'"),
    ):
        check_ai_worker("redis://localhost:6379/0", timeout_seconds=2.0)


def test_check_ai_worker_failure_question_count() -> None:
    store: dict[str, str] = {}

    def handle_rpush(key: str, val: str) -> None:
        data = json.loads(val)
        job_id = data["job_id"]
        result = {
            "status": "completed",
            "payload": {
                "primary_questions": [
                    {"candidate_id": "1", "text": "Question 1?", "evidence_ids": ["ev1"]},
                ]
            },
        }
        result["trace_id"] = data["trace_id"]
        store[f"virtujudge:local:result:{job_id}"] = json.dumps(result)

    fake_redis = FakeSyncRedis(store=store, rpush_handler=handle_rpush)

    with (
        patch("redis.from_url", return_value=fake_redis),
        pytest.raises(RuntimeError, match="Expected exactly 3 primary questions"),
    ):
        check_ai_worker("redis://localhost:6379/0", timeout_seconds=2.0)


def test_check_ai_worker_failure_ungrounded_question() -> None:
    store: dict[str, str] = {}

    def handle_rpush(key: str, val: str) -> None:
        data = json.loads(val)
        job_id = data["job_id"]
        result = {
            "status": "completed",
            "payload": {
                "primary_questions": [
                    {"candidate_id": "1", "text": "Question 1?", "evidence_ids": ["ev1"]},
                    {"candidate_id": "2", "text": "Question 2?", "evidence_ids": []},
                    {"candidate_id": "3", "text": "Question 3?", "evidence_ids": ["ev3"]},
                ]
            },
        }
        result["trace_id"] = data["trace_id"]
        store[f"virtujudge:local:result:{job_id}"] = json.dumps(result)

    fake_redis = FakeSyncRedis(store=store, rpush_handler=handle_rpush)

    with (
        patch("redis.from_url", return_value=fake_redis),
        pytest.raises(RuntimeError, match="not grounded"),
    ):
        check_ai_worker("redis://localhost:6379/0", timeout_seconds=2.0)


def test_ai_timeout_removes_queued_diagnostic() -> None:
    queue = FakeSyncRedis()
    with patch("redis.from_url", return_value=queue), pytest.raises(TimeoutError):
        check_ai_worker("redis://localhost:6379/0", timeout_seconds=0)
    assert all(not items for items in queue.lists.values())
    assert not queue.store


def test_ai_result_must_match_trace() -> None:
    queue = FakeSyncRedis()

    def deliver_wrong_trace(key: str, value: str) -> None:
        message = json.loads(value)
        queue.store[f"virtujudge:local:result:{message['job_id']}"] = json.dumps(
            {"trace_id": "another-job", "status": "completed"}
        )

    queue.rpush_handler = deliver_wrong_trace
    with (
        patch("redis.from_url", return_value=queue),
        pytest.raises(RuntimeError, match="correlation mismatch"),
    ):
        check_ai_worker("redis://localhost:6379/0")
