import asyncio
import urllib.parse
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import botocore.exceptions  # type: ignore[import-untyped]
import pytest
from pydantic import SecretStr

from app.domain.asset import (
    AssetSizeLimitExceeded,
    StorageUnavailable,
)
from app.infrastructure.storage.s3_object_storage import S3ObjectStorage
from app.settings import Settings


def test_s3_storage_signed_put_enforces_headers() -> None:
    settings = Settings(
        _env_file=None,
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
        object_storage_access_key=SecretStr("mock_access_key"),
        object_storage_secret_key=SecretStr("mock_secret_key"),
    )
    storage = S3ObjectStorage(settings)

    url, headers, expires_at = storage.generate_upload_url(
        storage_key="teams/t/projects/p/assets/a/v.pdf",
        content_type="application/pdf",
        content_length=2048,
        ttl_seconds=900,
    )

    assert headers["content-type"] == "application/pdf"
    assert headers["if-none-match"] == "*"
    assert expires_at > datetime.now(UTC)

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert "X-Amz-SignedHeaders" in query
    signed_headers = [h.strip().lower() for h in query["X-Amz-SignedHeaders"][0].split(";")]

    assert "content-type" in signed_headers
    assert "content-length" in signed_headers
    assert "if-none-match" in signed_headers


def test_s3_storage_signed_get_url() -> None:
    settings = Settings(
        _env_file=None,
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
        object_storage_access_key=SecretStr("mock_access_key"),
        object_storage_secret_key=SecretStr("mock_secret_key"),
    )
    storage = S3ObjectStorage(settings)

    url, expires_at = storage.generate_download_url(
        storage_key="teams/t/projects/p/assets/a/v.pdf",
        ttl_seconds=900,
    )

    assert "http://127.0.0.1:9000/virtujudge" in url
    assert expires_at > datetime.now(UTC)


def test_s3_storage_signing_error_handling() -> None:
    class FailingClient:
        def generate_presigned_url(self, **kwargs: object) -> str:
            err_dict = {
                "Error": {
                    "Code": "InvalidAccessKeyId",
                    "Message": "The AWS Access Key Id does not exist",
                }
            }
            raise botocore.exceptions.ClientError(err_dict, "generate_presigned_url")

    settings = Settings(
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
        object_storage_access_key=SecretStr("mock"),
        object_storage_secret_key=SecretStr("mock"),
    )
    storage = S3ObjectStorage(settings)
    storage._public_client = FailingClient()

    with pytest.raises(StorageUnavailable):
        storage.generate_upload_url("key", "application/pdf", 100, 900)

    with pytest.raises(StorageUnavailable):
        storage.generate_download_url("key", 900)


@pytest.mark.anyio
async def test_s3_storage_stream_to_disk_size_exceeded(tmp_path: Path) -> None:
    class FakeBody:
        def __init__(self, data: bytes):
            self.data = data
            self.offset = 0
            self.closed = False

        def read(self, chunk_size: int) -> bytes:
            chunk = self.data[self.offset : self.offset + chunk_size]
            self.offset += len(chunk)
            return chunk

        def close(self) -> None:
            self.closed = True

    fake_body = FakeBody(b"A" * 200)

    class FakeClient:
        def get_object(self, **kwargs: object) -> dict[str, object]:
            return {"Body": fake_body, "ContentType": "application/pdf"}

    settings = Settings(
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
        object_storage_access_key=SecretStr("mock"),
        object_storage_secret_key=SecretStr("mock"),
    )
    storage = S3ObjectStorage(settings)
    storage._internal_client = FakeClient()

    target = tmp_path / "out.pdf"
    with pytest.raises(AssetSizeLimitExceeded):
        await storage.stream_to_disk("key", target, max_bytes=100)

    assert fake_body.closed is True


@pytest.mark.anyio
async def test_s3_storage_midstream_read_failure_closes_body(tmp_path: Path) -> None:
    class BrokenBody:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        def read(self, chunk_size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"first_chunk"
            raise OSError("Socket connection reset")

        def close(self) -> None:
            self.closed = True

    broken_body = BrokenBody()

    class FakeClient:
        def get_object(self, **kwargs: object) -> dict[str, object]:
            return {"Body": broken_body, "ContentType": "application/pdf"}

    settings = Settings(
        object_storage_endpoint="http://127.0.0.1:9000",
        object_storage_bucket="virtujudge",
        object_storage_access_key=SecretStr("mock"),
        object_storage_secret_key=SecretStr("mock"),
    )
    storage = S3ObjectStorage(settings)
    storage._internal_client = FakeClient()

    target = tmp_path / "failed.pdf"
    with pytest.raises(StorageUnavailable):
        await storage.stream_to_disk("key", target, max_bytes=1000)

    assert broken_body.closed is True


def test_missing_storage_credentials_fail_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client(*args: object, **kwargs: object) -> None:
        pytest.fail("Missing credentials must not trigger provider credential discovery")

    monkeypatch.setattr(
        "app.infrastructure.storage.s3_object_storage.boto3.client", unexpected_client
    )
    storage = S3ObjectStorage(Settings(_env_file=None))
    with pytest.raises(StorageUnavailable):
        storage.generate_upload_url("key", "application/pdf", 4, 60)
    with pytest.raises(StorageUnavailable):
        storage.generate_download_url("key", 60)


@pytest.mark.anyio
async def test_cancelled_transfer_waits_for_writer_before_caller_unlinks(tmp_path: Path) -> None:
    entered, release, closed = Event(), Event(), Event()

    class Body(BytesIO):
        def close(self) -> None:
            super().close()
            closed.set()

    def get_object(**kwargs: object) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return {"Body": Body(b"synthetic private document"), "ContentType": "application/pdf"}

    storage = S3ObjectStorage(Settings(_env_file=None))
    storage._internal_client = Mock(get_object=get_object)
    target = tmp_path / "upload.pdf"
    task = asyncio.create_task(storage.stream_to_disk("synthetic-key", target, 1024))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    try:
        await asyncio.sleep(0.02)
        assert not task.done(), "Cancellation must wait until the storage writer has stopped"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 2)
        target.unlink(missing_ok=True)
    assert not target.exists()
