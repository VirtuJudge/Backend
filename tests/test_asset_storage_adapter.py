import urllib.parse
import zipfile
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
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage
from app.settings import Settings

PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


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


def create_synthetic_pptx(
    extra_files: dict[str, bytes] | None = None,
    override_files: dict[str, bytes] | None = None,
    corrupt_crc: bool = False,
) -> bytes:
    ct_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" '
        b'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/ppt/presentation.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        b'<Override PartName="/ppt/slides/slide1.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        b"</Types>"
    )
    root_rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" '
        b'Type="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships/officeDocument" '
        b'Target="ppt/presentation.xml"/>'
        b"</Relationships>"
    )
    pres_rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
        b'Target="slides/slide1.xml"/>'
        b"</Relationships>"
    )
    files = {
        "[Content_Types].xml": ct_xml,
        "_rels/.rels": root_rels,
        "ppt/presentation.xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            b'<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            b"</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": pres_rels,
        "ppt/slides/slide1.xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b"<p:cSld><p:spTree>"
            b'<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            b"<p:grpSpPr/>"
            b"</p:spTree></p:cSld>"
            b"</p:sld>"
        ),
    }
    if override_files:
        files.update(override_files)
    if extra_files:
        files.update(extra_files)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)

    val = buf.getvalue()
    if corrupt_crc:
        val = val[:50] + b"\xff\xff\xff\xff" + val[54:]
    return val


@pytest.mark.anyio
async def test_document_verifier_accepts_valid_pdf(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    pdf_path = tmp_path / "valid.pdf"
    pdf_path.write_bytes(create_synthetic_pdf())

    await verifier.verify_document(pdf_path, "application/pdf")


@pytest.mark.anyio
async def test_document_verifier_rejects_corrupt_pdf(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\ncorrupted file body without valid objects")

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_document(pdf_path, "application/pdf")
    assert "corrupt_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_document_verifier_rejects_encrypted_pdf(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(create_synthetic_pdf(encrypted=True))

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_document(pdf_path, "application/pdf")
    assert "encrypted_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_document_verifier_rejects_non_pdf_file(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    pdf_path = tmp_path / "text.pdf"
    pdf_path.write_bytes(b"This is a text file not starting with pdf magic bytes")

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_document(pdf_path, "application/pdf")
    assert "malformed_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_document_verifier_rejects_malformed_repaired_pdf(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    pdf_path = tmp_path / "broken_xref.pdf"
    valid_bytes = create_synthetic_pdf()
    corrupt_bytes = valid_bytes.replace(b"startxref", b"corruptxr")
    pdf_path.write_bytes(corrupt_bytes)

    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_document(pdf_path, "application/pdf")
    assert "corrupt_pdf" in str(exc_info.value)


@pytest.mark.anyio
async def test_document_verifier_accepts_valid_pptx(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "valid.pptx"
    p.write_bytes(create_synthetic_pptx())
    await verifier.verify_document(p, PPTX_TYPE)


@pytest.mark.anyio
async def test_document_verifier_accepts_pptx_with_harmless_names(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "harmless.pptx"
    p.write_bytes(
        create_synthetic_pptx(
            extra_files={
                "ppt/media/canvas.xml": b"<canvas/>",
                "ppt/navbar.png": b"fake image",
            }
        )
    )
    await verifier.verify_document(p, PPTX_TYPE)


@pytest.mark.anyio
async def test_document_verifier_accepts_pptx_with_internal_media(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "internal_media.pptx"
    slide_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        b"<p:cSld><p:spTree>"
        b"<p:pic><p:blipFill>"
        b'<a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        b'r:embed="rId2"/></p:blipFill></p:pic>'
        b"</p:spTree></p:cSld></p:sld>"
    )
    slide_rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId2" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        b'Target="../media/image.png"/>'
        b"</Relationships>"
    )
    p.write_bytes(
        create_synthetic_pptx(
            override_files={"ppt/slides/slide1.xml": slide_xml},
            extra_files={
                "ppt/slides/_rels/slide1.xml.rels": slide_rels,
                "ppt/media/image.png": b"\x89PNG\r\n\x1a\nfakeimage",
            },
        )
    )
    await verifier.verify_document(p, PPTX_TYPE)


@pytest.mark.anyio
async def test_document_verifier_accepts_pptx_with_external_hyperlink(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "external_link.pptx"
    slide_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        b"<p:cSld><p:spTree>"
        b'<p:sp><p:txBody><a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        b'<a:r><a:rPr><a:hlinkClick r:id="rId3"/></a:rPr><a:t>Link</a:t></a:r>'
        b"</a:p></p:txBody></p:sp>"
        b"</p:spTree></p:cSld></p:sld>"
    )
    slide_rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId3" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        b'Target="https://example.com" TargetMode="External"/>'
        b"</Relationships>"
    )
    p.write_bytes(
        create_synthetic_pptx(
            override_files={"ppt/slides/slide1.xml": slide_xml},
            extra_files={"ppt/slides/_rels/slide1.xml.rels": slide_rels},
        )
    )
    await verifier.verify_document(p, PPTX_TYPE)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "corrupt_data,expected_reason",
    [
        (b"not a zip file", "corrupt_pptx"),
        (create_synthetic_pptx(corrupt_crc=True), "corrupt_pptx"),
        (b"not-a-pptx-header" + create_synthetic_pptx(), "corrupt_pptx"),
        (
            create_synthetic_pptx(extra_files={"ppt/vbaProject.bin": b"macro"}),
            "macro_enabled_presentation",
        ),
        (
            create_synthetic_pptx(extra_files={"ppt\\..\\outside.xml": b"<xml/>"}),
            "unsafe_archive_path",
        ),
        (
            create_synthetic_pptx(extra_files={"/abs/path.xml": b"<xml/>"}),
            "unsafe_archive_path",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "ppt/presentation.xml": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                        b"<p:sldIdLst></p:sldIdLst></p:presentation>"
                    )
                }
            ),
            "empty_pptx",
        ),
        (
            create_synthetic_pptx(override_files={"ppt/presentation.xml": b"<corrupted><xml"}),
            "malformed_pptx",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "ppt/_rels/presentation.xml.rels": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        b'<Relationship Id="rId1" '
                        b'Type="http://schemas.openxmlformats.org/'
                        b'officeDocument/2006/relationships/slide" '
                        b'Target="slides/missing.xml"/>'
                        b"</Relationships>"
                    )
                }
            ),
            "broken_slide_reference",
        ),
        (
            create_synthetic_pptx(extra_files={"ppt/slides/_rels/slide1.xml.rels": b"<broken"}),
            "malformed_pptx",
        ),
        (
            create_synthetic_pptx(
                extra_files={
                    "ppt/slides/_rels/slide1.xml.rels": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        b'<Relationship Id="rId1" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        b'relationships/image" '
                        b'Target="../media/image.png"/>'
                        b'<Relationship Id="rId1" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        b'relationships/image" '
                        b'Target="../media/image2.png"/>'
                        b"</Relationships>"
                    )
                }
            ),
            "malformed_pptx",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "ppt/slides/slide1.xml": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b"<p:sld "
                        b'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                        b"<p:cSld><p:spTree>"
                        b"<p:pic><p:blipFill>"
                        b'<a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                        b'r:embed="rIdMissing"/></p:blipFill></p:pic>'
                        b"</p:spTree></p:cSld></p:sld>"
                    )
                }
            ),
            "broken_slide_reference",
        ),
        (
            create_synthetic_pptx(
                extra_files={
                    "ppt/slides/_rels/slide1.xml.rels": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        b'<Relationship Id="rId1" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        b'relationships/image" '
                        b'Target="../media/missing.png"/>'
                        b"</Relationships>"
                    )
                }
            ),
            "broken_slide_reference",
        ),
        (
            create_synthetic_pptx(
                extra_files={
                    "ppt/slides/_rels/slide1.xml.rels": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        b'<Relationship Id="rId2" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        b'relationships/image" '
                        b'Target="../../../outside.png"/>'
                        b"</Relationships>"
                    )
                }
            ),
            "unsafe_archive_path",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "[Content_Types].xml": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                        b'<Default Extension="rels" '
                        b'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                        b'<Default Extension="xml" ContentType="application/xml"/>'
                        b'<Override PartName="/ppt/presentation.xml" '
                        b'ContentType="application/vnd.openxmlformats-officedocument.'
                        b'presentationml.presentation.main+xml"/>'
                        b"</Types>"
                    )
                }
            ),
            "malformed_pptx",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "ppt/slides/slide1.xml": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
                    )
                }
            ),
            "broken_slide_reference",
        ),
        (
            create_synthetic_pptx(
                override_files={
                    "ppt/slides/slide1.xml": (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld/></p:sld>'
                    )
                }
            ),
            "broken_slide_reference",
        ),
    ],
)
async def test_document_verifier_rejects_invalid_pptx(
    tmp_path: Path, corrupt_data: bytes, expected_reason: str
) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "test.pptx"
    p.write_bytes(corrupt_data)
    with pytest.raises(AssetCorrupt) as exc_info:
        await verifier.verify_document(p, PPTX_TYPE)
    assert expected_reason in str(exc_info.value)


@pytest.mark.anyio
async def test_document_verifier_timeout_raises_storage_unavailable(tmp_path: Path) -> None:
    verifier = DocumentVerifier(timeout_seconds=0.0001)
    p = tmp_path / "timeout.pptx"
    p.write_bytes(create_synthetic_pptx())
    with pytest.raises(StorageUnavailable):
        await verifier.verify_document(p, PPTX_TYPE)


@pytest.mark.anyio
async def test_document_verifier_cancellation_reaps_child_process(tmp_path: Path) -> None:
    import asyncio

    verifier = DocumentVerifier(timeout_seconds=10.0)
    p = tmp_path / "cancel.pptx"
    p.write_bytes(create_synthetic_pptx())
    task = asyncio.create_task(verifier.verify_document(p, PPTX_TYPE))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


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
