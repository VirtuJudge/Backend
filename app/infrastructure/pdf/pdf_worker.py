import contextlib
import sys
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

import pypdf
import pypdf.errors

EXIT_OK = 0
EXIT_MALFORMED = 10
EXIT_ENCRYPTED = 11
EXIT_EMPTY = 12
EXIT_CORRUPT = 13
EXIT_INFRA = 20


def apply_resource_limits() -> None:
    if resource is not None:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))


def parse_and_validate(path_str: str) -> int:
    target = Path(path_str)
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(EXIT_INFRA)
    apply_resource_limits()
    sys.exit(parse_and_validate(sys.argv[1]))
