import asyncio
from pathlib import Path

import pytest

from app.domain.asset import (
    AssetCorrupt,
    StorageUnavailable,
)
from app.infrastructure.documents.document_verifier import DocumentVerifier
from tests.support.documents import (
    PPTX_TYPE,
    create_synthetic_pdf,
    create_synthetic_pptx,
)


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
    verifier = DocumentVerifier(timeout_seconds=10.0)
    p = tmp_path / "cancel.pptx"
    p.write_bytes(create_synthetic_pptx())
    task = asyncio.create_task(verifier.verify_document(p, PPTX_TYPE))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_document_verifier_accepts_large_pptx_with_large_embedded_media(tmp_path: Path) -> None:
    verifier = DocumentVerifier()
    p = tmp_path / "large_media.pptx"
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
        b'Target="../media/large_image.png"/>'
        b"</Relationships>"
    )
    # Create ~18MB PPTX with a 16MB image inside
    large_image = b"P" * (16 * 1024 * 1024)
    p.write_bytes(
        create_synthetic_pptx(
            override_files={"ppt/slides/slide1.xml": slide_xml},
            extra_files={
                "ppt/slides/_rels/slide1.xml.rels": slide_rels,
                "ppt/media/large_image.png": large_image,
            },
        )
    )
    await verifier.verify_document(p, PPTX_TYPE)


def test_document_verifier_default_timeout() -> None:
    verifier = DocumentVerifier()
    assert verifier.timeout_seconds == 60.0
