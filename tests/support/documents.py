import zipfile
from io import BytesIO

from pypdf import PdfWriter

PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def make_pdf(encrypted: bool = False, password: str = "pass") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt(password)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


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
