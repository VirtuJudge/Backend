from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from local_stack.smoke import (
    check_postgres,
    check_redis,
    check_role_isolation,
    check_s3,
)
from tests.support.smoke import FakeConnection, FakeCursor, FakeSyncRedis


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
