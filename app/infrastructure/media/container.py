from typing import Any


def extract_ebml_doctype(data: bytes) -> str | None:
    if len(data) < 4 or data[:4] != b"\x1a\x45\xdf\xa3":
        return None
    header_end = data.find(b"\x18\x53\x80\x67")
    limit = header_end if header_end != -1 else min(len(data), 4096)
    doctype_pos = data.find(b"\x42\x82", 4, limit)
    if doctype_pos == -1:
        return None
    pos = doctype_pos + 2
    if pos >= limit:
        return None
    first_byte = data[pos]
    if first_byte == 0:
        return None
    mask = 0x80
    vint_len = 1
    while mask and not (first_byte & mask):
        mask >>= 1
        vint_len += 1
    if pos + vint_len > limit:
        return None
    val = first_byte & (mask - 1)
    for i in range(1, vint_len):
        val = (val << 8) | data[pos + i]
    str_start = pos + vint_len
    str_end = str_start + val
    if str_end > len(data):
        return None
    return data[str_start:str_end].rstrip(b"\x00").decode("ascii", errors="ignore").lower()


def validate_mp4_brands(data: bytes, media_type: str) -> bool:
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(data[0:4], "big")
    box_size = min(box_size, len(data))
    major_brand = data[8:12].decode("latin1", errors="ignore")
    major_lower = major_brand.lower().strip()
    if major_lower.startswith(("3gp", "3g2", "3ge", "3gg")) or major_lower == "qt":
        return False
    brands = {major_brand.strip()}
    for i in range(16, box_size - 3, 4):
        brand = data[i : i + 4].decode("latin1", errors="ignore").strip()
        if brand:
            brands.add(brand)
    brands_lower = {b.lower() for b in brands}
    if media_type == "video/mp4":
        allowed = {"mp41", "mp42", "isom", "iso2", "iso3", "iso4", "iso5", "iso6", "avc1", "dash"}
        return bool(brands_lower & allowed)
    if media_type == "audio/mp4":
        allowed = {
            "m4a",
            "m4b",
            "mp41",
            "mp42",
            "isom",
            "iso2",
            "iso3",
            "iso4",
            "iso5",
            "iso6",
            "dash",
        }
        return bool(brands_lower & allowed)
    return False


_extract_ebml_doctype = extract_ebml_doctype
_validate_mp4_brands = validate_mp4_brands


MEDIA_RULES: dict[str, dict[str, Any]] = {
    "presentation_video": {
        "max_duration_ms": 600_000,
        "media_types": {
            "video/mp4": {
                "demuxer": "mov,mp4,m4a,3gp,3g2,mj2",
                "containers": {"mov,mp4,m4a,3gp,3g2,mj2", "mp4", "mov", "m4a"},
                "video_codecs": {"h264", "hevc", "av1", "vp9"},
                "audio_codecs": {"aac", "mp3", "opus", "flac"},
                "signature_check": lambda data: validate_mp4_brands(data, "video/mp4"),
            },
            "video/webm": {
                "demuxer": "matroska,webm",
                "containers": {"matroska,webm", "webm", "matroska"},
                "video_codecs": {"vp8", "vp9", "av1"},
                "audio_codecs": {"opus", "vorbis"},
                "signature_check": lambda data: extract_ebml_doctype(data) == "webm",
            },
        },
    },
    "answer_audio": {
        "max_duration_ms": 120_000,
        "media_types": {
            "audio/webm": {
                "demuxer": "matroska,webm",
                "containers": {"matroska,webm", "webm", "matroska"},
                "video_codecs": set(),
                "audio_codecs": {"opus", "vorbis"},
                "signature_check": lambda data: extract_ebml_doctype(data) == "webm",
            },
            "audio/ogg": {
                "demuxer": "ogg",
                "containers": {"ogg"},
                "video_codecs": set(),
                "audio_codecs": {"opus", "vorbis", "flac"},
                "signature_check": lambda data: len(data) >= 4 and data[:4] == b"OggS",
            },
            "audio/mp4": {
                "demuxer": "mov,mp4,m4a,3gp,3g2,mj2",
                "containers": {"mov,mp4,m4a,3gp,3g2,mj2", "mp4", "mov", "m4a"},
                "video_codecs": set(),
                "audio_codecs": {"aac", "mp3", "opus", "flac", "alac"},
                "signature_check": lambda data: validate_mp4_brands(data, "audio/mp4"),
            },
            "audio/wav": {
                "demuxer": "wav",
                "containers": {"wav"},
                "video_codecs": set(),
                "audio_codecs": {
                    "pcm_s16le",
                    "pcm_s24le",
                    "pcm_s32le",
                    "pcm_u8",
                    "pcm_f32le",
                    "pcm_alaw",
                    "pcm_mulaw",
                },
                "signature_check": lambda data: (
                    len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
                ),
            },
        },
    },
}
