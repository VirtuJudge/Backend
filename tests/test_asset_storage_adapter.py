import urllib.parse
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import botocore.exceptions  # type: ignore[import-untyped]
import pytest
from pydantic import SecretStr
from pypdf import PdfWriter

from app.domain.asset import (
    AssetCorrupt,
    AssetSizeLimitExceeded,
    StorageUnavailable,
)
from app.infrastructure.pdf.pypdfVerifier import PyPdfVerifier
from app.infrastructure.settings import Settings
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage


def create_synthetic_pdf(
    encrypted: bool = False,
    password: str = "password",
    pages: int = 1,
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt(password)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.anyio
async def test_pypdf_verifier_accepts_valid_pdf(tmp_path: Path) -> None:
    verifier = PyPdfVerifier()
    pdf_path = tmp_path / "valid.pdf"
    pdf_path.write_bytes(create_synthetic_pdf())

    await verifier.verify_pdf(pdf_path)


@pytest.mark.anyio
async def test_pypdf_verifier_rejects_corrupt_pdf(tmp_path: Path) -> None:
    verifier = PyPdfVerifier()
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\ncorrupted file body without valid objects")

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_pdf(pdf_path)
    assert "corrupt_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_pypdf_verifier_rejects_encrypted_pdf(tmp_path: Path) -> None:
    verifier = PyPdfVerifier()
    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(create_synthetic_pdf(encrypted=True))

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_pdf(pdf_path)
    assert "encrypted_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_pypdf_verifier_rejects_non_pdf_file(tmp_path: Path) -> None:
    verifier = PyPdfVerifier()
    pdf_path = tmp_path / "text.pdf"
    pdf_path.write_bytes(b"This is a text file not starting with pdf magic bytes")

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_pdf(pdf_path)
    assert "malformed_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_pypdf_verifier_rejects_malformed_repaired_pdf(tmp_path: Path) -> None:
    verifier = PyPdfVerifier()
    pdf_path = tmp_path / "broken_xref.pdf"
    valid_bytes = create_synthetic_pdf()
    # Damage the xref pointer near the end of the file
    corrupt_bytes = valid_bytes.replace(b"startxref", b"corruptxr")
    pdf_path.write_bytes(corrupt_bytes)

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_pdf(pdf_path)
    assert "corrupt_pdf" in str(exc_info.value)


def test_s3_storage_signed_put_enforces_headers() -> None:
    settings = Settings(
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
        "app.infrastructure.storage.s3ObjectStorage.boto3.client", unexpected_client
    )
    storage = S3ObjectStorage(Settings(_env_file=None))
    with pytest.raises(StorageUnavailable):
        storage.generate_upload_url("key", "application/pdf", 4, 60)
    with pytest.raises(StorageUnavailable):
        storage.generate_download_url("key", 60)
