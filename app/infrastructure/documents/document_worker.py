import contextlib
import posixpath
import sys
import zipfile
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

import defusedxml.ElementTree as ET  # type: ignore[import-untyped]
import pypdf
import pypdf.errors

EXIT_OK = 0
EXIT_MALFORMED = 10
EXIT_ENCRYPTED = 11
EXIT_EMPTY = 12
EXIT_CORRUPT = 13
EXIT_ZIP_BOMB = 14
EXIT_UNSAFE_PATH = 15
EXIT_DUPLICATE_PATHS = 16
EXIT_MACROS = 17
EXIT_BROKEN_SLIDES = 18
EXIT_INFRA = 20

MAX_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024
MAX_SINGLE_XML_BYTES = 15 * 1024 * 1024

NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_OFFICE_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PRESENTATION = "http://schemas.openxmlformats.org/presentationml/2006/main"

MAIN_PRESENTATION_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)
REL_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"


def apply_resource_limits() -> None:
    if resource is not None:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))


def validate_pdf(target: Path) -> int:
    try:
        with target.open("rb") as f:
            header = f.read(1024)
            if not header.startswith(b"%PDF-"):
                return EXIT_MALFORMED
    except OSError:
        return EXIT_CORRUPT

    try:
        reader = pypdf.PdfReader(str(target), strict=True)
        if reader.is_encrypted:
            return EXIT_ENCRYPTED
        if len(reader.pages) == 0:
            return EXIT_EMPTY
        for page in reader.pages:
            _ = page.mediabox
            contents = page.get_contents()
            if contents is not None:
                if hasattr(contents, "get_data"):
                    _ = contents.get_data()
                elif isinstance(contents, list):
                    for obj in contents:
                        if hasattr(obj, "get_data"):
                            _ = obj.get_data()
            _ = page.extract_text()
        return EXIT_OK
    except (
        pypdf.errors.PdfReadError,
        pypdf.errors.PyPdfError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
    ):
        return EXIT_CORRUPT
    except Exception:
        return EXIT_INFRA


def is_unsafe_archive_path(filename: str) -> bool:
    if not filename or not filename.strip() or "\x00" in filename:
        return True
    norm = filename.replace("\\", "/")
    if norm.startswith("/"):
        return True
    parts = norm.split("/")
    return any(part == ".." or (len(part) > 1 and part[1] == ":") for part in parts)


def validate_pptx(target: Path) -> int:
    try:
        if not zipfile.is_zipfile(target):
            return EXIT_CORRUPT
        zf = zipfile.ZipFile(target, "r")
    except (zipfile.BadZipFile, OSError):
        return EXIT_CORRUPT

    with zf:
        infolist = zf.infolist()
        if not infolist:
            return EXIT_EMPTY

        names = [info.filename for info in infolist]

        if len(names) != len(set(names)):
            return EXIT_DUPLICATE_PATHS

        if len(infolist) > MAX_ENTRIES:
            return EXIT_ZIP_BOMB

        total_uncompressed = sum(info.file_size for info in infolist)
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
            return EXIT_ZIP_BOMB

        for info in infolist:
            if info.flag_bits & 0x1:
                return EXIT_ENCRYPTED
            if is_unsafe_archive_path(info.filename):
                return EXIT_UNSAFE_PATH
            if info.file_size > MAX_SINGLE_XML_BYTES:
                return EXIT_ZIP_BOMB
            if (
                info.file_size > 1024
                and info.compress_size > 0
                and (info.file_size / info.compress_size) > 100
            ):
                return EXIT_ZIP_BOMB
            lower_name = info.filename.lower().replace("\\", "/")
            base_name = posixpath.basename(lower_name)
            if base_name in ("vbaproject.bin", "vba.bin") or base_name.endswith(
                (".vba", ".bas", ".cls")
            ):
                return EXIT_MACROS

        try:
            bad_crc_file = zf.testzip()
            if bad_crc_file is not None:
                return EXIT_CORRUPT
        except Exception:
            return EXIT_CORRUPT

        if "[Content_Types].xml" not in names or "_rels/.rels" not in names:
            return EXIT_MALFORMED

        try:
            content_types_xml = zf.read("[Content_Types].xml")
            ct_root = ET.fromstring(content_types_xml)
        except Exception:
            return EXIT_MALFORMED

        if ct_root.tag != f"{{{NS_CONTENT_TYPES}}}Types":
            return EXIT_MALFORMED

        has_main_type = False
        for elem in ct_root:
            ct = elem.get("ContentType", "")
            if "macroenabled" in ct.lower() or "vbaproject" in ct.lower():
                return EXIT_MACROS
            tag = elem.tag.split("}")[-1]
            if tag == "Override":
                part_name = elem.get("PartName", "")
                norm_part = part_name.replace("\\", "/").lstrip("/")
                if norm_part == "ppt/presentation.xml" and ct == MAIN_PRESENTATION_TYPE:
                    has_main_type = True

        if not has_main_type:
            return EXIT_MALFORMED

        try:
            root_rels_xml = zf.read("_rels/.rels")
            root_rels = ET.fromstring(root_rels_xml)
        except Exception:
            return EXIT_MALFORMED

        if root_rels.tag != f"{{{NS_PACKAGE_RELS}}}Relationships":
            return EXIT_MALFORMED

        presentation_part = None
        seen_rel_ids: set[str] = set()
        for elem in root_rels:
            tag = elem.tag.split("}")[-1]
            if tag == "Relationship":
                rel_id = elem.get("Id", "")
                if not rel_id or rel_id in seen_rel_ids:
                    return EXIT_MALFORMED
                seen_rel_ids.add(rel_id)

                rel_type = elem.get("Type", "")
                target_mode = elem.get("TargetMode", "")
                target_val = elem.get("Target", "")
                if rel_type == REL_OFFICE_DOCUMENT:
                    if target_mode.lower() == "external":
                        return EXIT_MALFORMED
                    if is_unsafe_archive_path(target_val):
                        return EXIT_UNSAFE_PATH
                    presentation_part = posixpath.normpath(
                        target_val.replace("\\", "/").lstrip("/")
                    )

        if presentation_part != "ppt/presentation.xml" or presentation_part not in names:
            return EXIT_MALFORMED

        try:
            presentation_xml = zf.read("ppt/presentation.xml")
            pres_root = ET.fromstring(presentation_xml)
        except Exception:
            return EXIT_MALFORMED

        if pres_root.tag != f"{{{NS_PRESENTATION}}}presentation":
            return EXIT_MALFORMED

        slide_id_list = None
        for elem in pres_root:
            if elem.tag.split("}")[-1] == "sldIdLst":
                slide_id_list = elem
                break

        if slide_id_list is None:
            return EXIT_EMPTY

        slide_rids: list[str] = []
        for elem in slide_id_list:
            if elem.tag.split("}")[-1] == "sldId":
                rid = elem.get(f"{{{NS_OFFICE_RELS}}}id") or elem.get("r:id") or elem.get("id")
                if rid:
                    slide_rids.append(rid)

        if not slide_rids:
            return EXIT_EMPTY

        pres_rels_path = "ppt/_rels/presentation.xml.rels"
        if pres_rels_path not in names:
            return EXIT_BROKEN_SLIDES

        try:
            pres_rels_xml = zf.read(pres_rels_path)
            pres_rels_root = ET.fromstring(pres_rels_xml)
        except Exception:
            return EXIT_BROKEN_SLIDES

        if pres_rels_root.tag != f"{{{NS_PACKAGE_RELS}}}Relationships":
            return EXIT_BROKEN_SLIDES

        rels_map: dict[str, tuple[str, str, str]] = {}
        seen_pres_rel_ids: set[str] = set()
        for elem in pres_rels_root:
            if elem.tag.split("}")[-1] == "Relationship":
                rel_id = elem.get("Id", "")
                if not rel_id or rel_id in seen_pres_rel_ids:
                    return EXIT_BROKEN_SLIDES
                seen_pres_rel_ids.add(rel_id)

                rel_type = elem.get("Type", "")
                rel_target = elem.get("Target", "")
                rel_mode = elem.get("TargetMode", "")
                if "vbaproject" in rel_type.lower():
                    return EXIT_MACROS
                rels_map[rel_id] = (rel_type, rel_target, rel_mode)

        for rid in slide_rids:
            if rid not in rels_map:
                return EXIT_BROKEN_SLIDES
            rel_type, rel_target, rel_mode = rels_map[rid]
            if rel_type != REL_SLIDE or rel_mode.lower() == "external":
                return EXIT_BROKEN_SLIDES

            if is_unsafe_archive_path(rel_target):
                return EXIT_UNSAFE_PATH
            resolved = posixpath.normpath(posixpath.join("ppt", rel_target.replace("\\", "/")))
            if not resolved.startswith("ppt/"):
                return EXIT_UNSAFE_PATH
            if resolved not in names:
                return EXIT_BROKEN_SLIDES

            try:
                slide_xml = zf.read(resolved)
                slide_root = ET.fromstring(slide_xml)
                if slide_root.tag != f"{{{NS_PRESENTATION}}}sld":
                    return EXIT_BROKEN_SLIDES
            except Exception:
                return EXIT_BROKEN_SLIDES

        return EXIT_OK


def main() -> int:
    if len(sys.argv) < 3:
        return EXIT_INFRA

    path_str = sys.argv[1]
    media_type = sys.argv[2].strip().lower()

    apply_resource_limits()
    target = Path(path_str)

    if media_type == "application/pdf":
        return validate_pdf(target)
    if media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return validate_pptx(target)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
