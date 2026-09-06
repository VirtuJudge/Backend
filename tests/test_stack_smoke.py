import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from psycopg.errors import InsufficientPrivilege

from local_stack.smoke import (
    check_ai_worker,
    check_postgres,
    check_redis,
    check_role_isolation,
    check_s3,
    main,
)


class FakeCursor:
    def __init__(
        self,
        select_1_result: tuple[int, ...] = (1,),
        pgvector_result: tuple[str, ...] = ("vector",),
        cross_schema_fail: bool = True,
    ) -> None:
        self.select_1_result = select_1_result
        self.pgvector_result = pgvector_result
        self.cross_schema_fail = cross_schema_fail
        self.executed_statements: list[str] = []
        self._current_result: Any = None

    def execute(self, query: str, params: Any = None) -> None:
        self.executed_statements.append(query)
        if "SELECT 1" in query and "FROM" not in query:
            self._current_result = self.select_1_result
        elif "pg_extension" in query:
            self._current_result = self.pgvector_result
        elif "probe_table" in query:
            if self.cross_schema_fail:
                raise InsufficientPrivilege("permission denied for schema")
            self._current_result = (1,)
        elif "SELECT note FROM" in query:
            self._current_result = ("backend_ok",)

    def fetchone(self) -> Any:
        return self._current_result

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class FakeSyncRedis:
    def __init__(
        self,
        store: dict[str, str] | None = None,
        ping_result: bool = True,
        rpush_handler: Any | None = None,
    ) -> None:
        self.store = store if store is not None else {}
        self.ttls: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.ping_result = ping_result
        self.deleted_keys: list[str] = []
        self.rpush_handler = rpush_handler

    def ping(self) -> bool:
        return self.ping_result

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, *keys: str) -> int:
        for k in keys:
            self.store.pop(k, None)
            self.deleted_keys.append(k)
        return len(keys)

    def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        if value in items:
            items.remove(value)
            return 1
        return 0

    def rpush(self, key: str, value: str) -> int:
        if self.rpush_handler:
            self.rpush_handler(key, value)
        if key not in self.lists:
            self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])

    def close(self) -> None:
        pass


def test_check_postgres_success() -> None:
    fake_cursor = FakeCursor()
    fake_conn = FakeConnection(fake_cursor)
    with patch("psycopg.connect", return_value=fake_conn):
        check_postgres("postgresql://test:test@localhost:5432/test")
    assert any("SELECT 1" in s for s in fake_cursor.executed_statements)
    assert any("pg_extension" in s for s in fake_cursor.executed_statements)


def test_check_postgres_missing_extension_raises() -> None:
    fake_cursor = FakeCursor(pgvector_result=())
    fake_conn = FakeConnection(fake_cursor)
    with patch("psycopg.connect", return_value=fake_conn), pytest.raises(RuntimeError):
        check_postgres("postgresql://test:test@localhost:5432/test")


def test_check_role_isolation_success() -> None:
    fake_cursor = FakeCursor(cross_schema_fail=True)
    fake_conn = FakeConnection(fake_cursor)
    with patch("psycopg.connect", return_value=fake_conn):
        check_role_isolation("postgresql://test:test@localhost:5432/test", "backend", "ai")
    assert any("CREATE TABLE backend." in s for s in fake_cursor.executed_statements)
    assert any("DROP TABLE IF EXISTS backend." in s for s in fake_cursor.executed_statements)


def test_check_role_isolation_fails_if_no_insufficient_privilege() -> None:
    fake_cursor = FakeCursor(cross_schema_fail=False)
    fake_conn = FakeConnection(fake_cursor)
    with patch("psycopg.connect", return_value=fake_conn), pytest.raises(RuntimeError):
        check_role_isolation("postgresql://test:test@localhost:5432/test", "backend", "ai")
    # Must still drop table in finally
    assert any("DROP TABLE IF EXISTS backend." in s for s in fake_cursor.executed_statements)


def test_check_redis_success() -> None:
    fake_redis = FakeSyncRedis()
    with patch("redis.from_url", return_value=fake_redis) as mock_from_url:
        check_redis("redis://localhost:6379/0")
        mock_from_url.assert_called_once()
        called_url = mock_from_url.call_args[0][0]
        assert called_url.endswith("/1")
    assert len(fake_redis.deleted_keys) > 0


def test_check_redis_ping_failure() -> None:
    fake_redis = FakeSyncRedis(ping_result=False)
    with (
        patch("redis.from_url", return_value=fake_redis),
        pytest.raises(RuntimeError, match="ping failed"),
    ):
        check_redis("redis://localhost:6379/0")


def test_check_s3_success() -> None:
    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.__enter__.return_value = mock_body
    mock_body.read.return_value = b"synthetic-smoke-payload"
    mock_s3.get_object.return_value = {"Body": mock_body}

    with patch("boto3.client", return_value=mock_s3) as mock_boto:
        check_s3("http://localhost:9000", "virtujudge", "testuser", "testpass")
        mock_boto.assert_called_once()
        _args, kwargs = mock_boto.call_args
        assert kwargs["aws_access_key_id"] == "testuser"
        assert kwargs["aws_secret_access_key"] == "testpass"
        assert kwargs["config"].s3["addressing_style"] == "path"

    mock_s3.create_bucket.assert_called_once_with(Bucket="virtujudge")
    mock_s3.put_object.assert_called_once()
    mock_s3.delete_object.assert_called_once()


def test_check_s3_handles_existing_bucket_idempotently() -> None:
    mock_s3 = MagicMock()
    mock_s3.create_bucket.side_effect = ClientError(
        {"Error": {"Code": "BucketAlreadyOwnedByYou"}}, "CreateBucket"
    )
    mock_body = MagicMock()
    mock_body.__enter__.return_value = mock_body
    mock_body.read.return_value = b"synthetic-smoke-payload"
    mock_s3.get_object.return_value = {"Body": mock_body}

    with patch("boto3.client", return_value=mock_s3):
        check_s3("http://localhost:9000", "virtujudge", "testuser", "testpass")
    mock_s3.put_object.assert_called_once()


def test_check_s3_arbitrary_denial_raises() -> None:
    mock_s3 = MagicMock()
    mock_s3.create_bucket.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "CreateBucket"
    )

    with (
        patch("boto3.client", return_value=mock_s3),
        pytest.raises(ClientError),
    ):
        check_s3("http://localhost:9000", "virtujudge", "testuser", "testpass")


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


def test_main_cli_failure_hides_connection_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic")
    with patch("psycopg.connect", side_effect=RuntimeError("private connection details")):
        assert main(["backend"]) == 1
    assert "private connection details" not in capsys.readouterr().err


def test_local_stack_sh_smoke_calls_docker_exec(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "local-stack.sh"
    shutil.copyfile(Path(__file__).parents[1] / "scripts" / script.name, script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    log_file = tmp_path / "docker_cmds.log"
    docker.write_text(f'#!/bin/sh\necho "$@" >> "{log_file}"\nexit 0\n', encoding="utf-8")
    docker.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    # 1. Default smoke runs all (backend and ai)
    subprocess.run(
        ["bash", str(script), "smoke"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T backend python -m local_stack.smoke backend" in content
    assert "exec -T ai-worker python -m local_stack.smoke ai" in content

    # 2. smoke backend runs only backend
    log_file.unlink()
    subprocess.run(
        ["bash", str(script), "smoke", "backend"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T backend python -m local_stack.smoke backend" in content
    assert "ai-worker" not in content

    # 3. smoke ai runs only ai
    log_file.unlink()
    subprocess.run(
        ["bash", str(script), "smoke", "ai"],
        env=environment,
        check=True,
        cwd=str(tmp_path),
    )
    content = log_file.read_text(encoding="utf-8")
    assert "exec -T ai-worker python -m local_stack.smoke ai" in content
    assert "backend" not in content


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
