import contextlib
import posixpath
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote

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
SLIDE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
REL_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"


def apply_resource_limits() -> None:
    if resource is not None:
        resource_api = vars(resource)
        with contextlib.suppress(ValueError, OSError):
            resource_api["setrlimit"](
                resource_api["RLIMIT_AS"],
                (256 * 1024 * 1024, 256 * 1024 * 1024),
            )
        with contextlib.suppress(ValueError, OSError):
            resource_api["setrlimit"](resource_api["RLIMIT_CPU"], (5, 5))


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


def resolve_rel_target(source_part: str, target: str) -> tuple[str, bool]:
    if not target or "\x00" in target:
        return ("", True)
    decoded = unquote(target)
    if "\x00" in decoded:
        return ("", True)
    norm = decoded.strip().replace("\\", "/")
    if norm.startswith("/"):
        resolved = posixpath.normpath(norm.lstrip("/"))
    else:
        source_dir = posixpath.dirname(source_part)
        resolved = posixpath.normpath(posixpath.join(source_dir, norm))

    if resolved.startswith("..") or resolved == ".." or resolved.startswith("/"):
        return ("", True)
    parts = resolved.split("/")
    if any(part == ".." or (len(part) > 1 and part[1] == ":") for part in parts):
        return ("", True)
    return (resolved, False)


def validate_pptx(target: Path) -> int:
    try:
        with target.open("rb") as file:
            if file.read(4) != b"PK\x03\x04":
                return EXIT_CORRUPT
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

        rels_by_path: dict[str, dict[str, tuple[str, str, str]]] = {}
        for name in names:
            if name.endswith(".rels"):
                try:
                    raw_rels = zf.read(name)
                    root_elem = ET.fromstring(raw_rels)
                except Exception:
                    return EXIT_MALFORMED

                if root_elem.tag != f"{{{NS_PACKAGE_RELS}}}Relationships":
                    return EXIT_MALFORMED

                file_rels: dict[str, tuple[str, str, str]] = {}
                for elem in root_elem:
                    if elem.tag != f"{{{NS_PACKAGE_RELS}}}Relationship":
                        return EXIT_MALFORMED
                    rid = elem.get("Id", "")
                    if not rid or rid in file_rels:
                        return EXIT_MALFORMED
                    rtype = elem.get("Type", "")
                    if "vbaproject" in rtype.lower():
                        return EXIT_MACROS
                    rtarget = elem.get("Target", "")
                    rmode = elem.get("TargetMode", "")
                    file_rels[rid] = (rtype, rtarget, rmode)
                rels_by_path[name] = file_rels

        try:
            content_types_xml = zf.read("[Content_Types].xml")
            ct_root = ET.fromstring(content_types_xml)
        except Exception:
            return EXIT_MALFORMED

        if ct_root.tag != f"{{{NS_CONTENT_TYPES}}}Types":
            return EXIT_MALFORMED

        defaults: dict[str, str] = {}
        overrides: dict[str, str] = {}
        for elem in ct_root:
            ct = elem.get("ContentType", "")
            if "macroenabled" in ct.lower() or "vbaproject" in ct.lower():
                return EXIT_MACROS
            if elem.tag == f"{{{NS_CONTENT_TYPES}}}Default":
                ext = elem.get("Extension", "").lower()
                if ext:
                    defaults[ext] = ct
            elif elem.tag == f"{{{NS_CONTENT_TYPES}}}Override":
                part_name = elem.get("PartName", "")
                norm_part = posixpath.normpath(part_name.replace("\\", "/").lstrip("/"))
                if norm_part:
                    overrides[norm_part] = ct

        def get_content_type(part_path: str) -> str:
            norm = posixpath.normpath(part_path.replace("\\", "/").lstrip("/"))
            if norm in overrides:
                return overrides[norm]
            ext = posixpath.splitext(norm)[1].lstrip(".").lower()
            return defaults.get(ext, "")

        if get_content_type("ppt/presentation.xml") != MAIN_PRESENTATION_TYPE:
            return EXIT_MALFORMED

        root_rels = rels_by_path.get("_rels/.rels")
        if root_rels is None:
            return EXIT_MALFORMED

        presentation_part = None
        for rtype, rtarget, rmode in root_rels.values():
            if rtype == REL_OFFICE_DOCUMENT:
                if rmode.lower() == "external":
                    return EXIT_MALFORMED
                resolved_pres, is_unsafe = resolve_rel_target("", rtarget)
                if is_unsafe:
                    return EXIT_UNSAFE_PATH
                presentation_part = resolved_pres

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
            if elem.tag == f"{{{NS_PRESENTATION}}}sldIdLst":
                slide_id_list = elem
                break

        if slide_id_list is None:
            return EXIT_EMPTY

        slide_rids: list[str] = []
        for elem in slide_id_list:
            if elem.tag == f"{{{NS_PRESENTATION}}}sldId":
                rid = elem.get(f"{{{NS_OFFICE_RELS}}}id") or elem.get("r:id") or elem.get("id")
                if rid:
                    slide_rids.append(rid)

        if not slide_rids:
            return EXIT_EMPTY

        pres_rels_path = "ppt/_rels/presentation.xml.rels"
        if pres_rels_path not in rels_by_path:
            return EXIT_BROKEN_SLIDES

        pres_rels = rels_by_path[pres_rels_path]
        for rid in slide_rids:
            if rid not in pres_rels:
                return EXIT_BROKEN_SLIDES
            rel_type, rel_target, rel_mode = pres_rels[rid]
            if rel_type != REL_SLIDE or rel_mode.lower() == "external":
                return EXIT_BROKEN_SLIDES

            resolved, is_unsafe = resolve_rel_target("ppt/presentation.xml", rel_target)
            if is_unsafe or not resolved.startswith("ppt/"):
                return EXIT_UNSAFE_PATH
            if resolved not in names:
                return EXIT_BROKEN_SLIDES

            if get_content_type(resolved) != SLIDE_CONTENT_TYPE:
                return EXIT_MALFORMED

            try:
                slide_xml = zf.read(resolved)
                slide_root = ET.fromstring(slide_xml)
            except Exception:
                return EXIT_MALFORMED

            if slide_root.tag != f"{{{NS_PRESENTATION}}}sld":
                return EXIT_BROKEN_SLIDES

            csld = None
            for child in slide_root:
                if child.tag == f"{{{NS_PRESENTATION}}}cSld":
                    csld = child
                    break
            if csld is None:
                return EXIT_BROKEN_SLIDES

            sptree = None
            for child in csld:
                if child.tag == f"{{{NS_PRESENTATION}}}spTree":
                    sptree = child
                    break
            if sptree is None:
                return EXIT_BROKEN_SLIDES

            slide_dir = posixpath.dirname(resolved)
            slide_base = posixpath.basename(resolved)
            slide_rels_path = posixpath.join(slide_dir, "_rels", f"{slide_base}.rels")
            slide_rels = rels_by_path.get(slide_rels_path)

            used_rel_ids: set[str] = set()
            for elem in slide_root.iter():
                for attr_name, attr_val in elem.attrib.items():
                    if (
                        attr_name.startswith(f"{{{NS_OFFICE_RELS}}}") or attr_name.startswith("r:")
                    ) and attr_val.strip():
                        used_rel_ids.add(attr_val.strip())

            if used_rel_ids:
                if slide_rels is None:
                    return EXIT_BROKEN_SLIDES
                for uid in used_rel_ids:
                    if uid not in slide_rels:
                        return EXIT_BROKEN_SLIDES

            if slide_rels is not None:
                for _s_type, s_target, s_mode in slide_rels.values():
                    if s_mode.lower() == "external":
                        continue
                    t_resolved, t_unsafe = resolve_rel_target(resolved, s_target)
                    if t_unsafe:
                        return EXIT_UNSAFE_PATH
                    if t_resolved not in names:
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
