from app.infrastructure.media.container import (
    _extract_ebml_doctype,
    _validate_mp4_brands,
)


def test_ebml_and_brand_helpers() -> None:
    assert _extract_ebml_doctype(b"short") is None
    assert _extract_ebml_doctype(b"\x1a\x45\xdf\xa3\x9f\x42\x82\x84webm") == "webm"
    assert _extract_ebml_doctype(b"\x1a\x45\xdf\xa3\xa3\x42\x82\x88matroska") == "matroska"

    assert not _validate_mp4_brands(b"short", "video/mp4")
    # QuickTime brand rejection
    qt_box = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  "
    assert not _validate_mp4_brands(qt_box, "video/mp4")
    # 3GP brand rejection
    t3gp_box = b"\x00\x00\x00\x1cftyp3gp4\x00\x00\x02\x003gp4isom"
    assert not _validate_mp4_brands(t3gp_box, "video/mp4")
    # Valid MP4 isom box
    isom_box = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert _validate_mp4_brands(isom_box, "video/mp4")
