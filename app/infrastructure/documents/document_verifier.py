import asyncio
import sys
from pathlib import Path

from app.application.interfaces.documentVerifier import DocumentVerifierPort
from app.domain.asset import AssetCorrupt, AssetUnsupportedMediaType, StorageUnavailable
from app.infrastructure.documents.document_worker import (
    EXIT_BROKEN_SLIDES,
    EXIT_CORRUPT,
    EXIT_DUPLICATE_PATHS,
    EXIT_EMPTY,
    EXIT_ENCRYPTED,
    EXIT_MACROS,
    EXIT_MALFORMED,
    EXIT_OK,
    EXIT_UNSAFE_PATH,
    EXIT_ZIP_BOMB,
)


class DocumentVerifier(DocumentVerifierPort):
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    async def verify_document(self, file_path: Path, media_type: str) -> None:
        normalized_type = media_type.strip().lower()
        if normalized_type not in (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ):
            raise AssetUnsupportedMediaType(f"Unsupported media type: {media_type}")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.infrastructure.documents.document_worker",
                str(file_path.resolve()),
                normalized_type,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            returncode = await asyncio.wait_for(proc.wait(), timeout=self.timeout_seconds)
        except TimeoutError as err:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise StorageUnavailable("Document verification service timed out") from err
        except asyncio.CancelledError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise
        except OSError as err:
            raise StorageUnavailable("Failed to start document verification worker") from err

        if returncode == EXIT_OK:
            return

        is_pptx = (
            normalized_type
            == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

        if returncode == EXIT_MALFORMED:
            raise AssetCorrupt("malformed_pptx" if is_pptx else "malformed_pdf")
        if returncode == EXIT_ENCRYPTED:
            raise AssetCorrupt("encrypted_pptx" if is_pptx else "encrypted_pdf")
        if returncode == EXIT_EMPTY:
            raise AssetCorrupt("empty_pptx" if is_pptx else "empty_pdf")
        if returncode == EXIT_CORRUPT:
            raise AssetCorrupt("corrupt_pptx" if is_pptx else "corrupt_pdf")
        if returncode == EXIT_ZIP_BOMB:
            raise AssetCorrupt("zip_bomb")
        if returncode == EXIT_UNSAFE_PATH:
            raise AssetCorrupt("unsafe_archive_path")
        if returncode == EXIT_DUPLICATE_PATHS:
            raise AssetCorrupt("duplicate_archive_paths")
        if returncode == EXIT_MACROS:
            raise AssetCorrupt("macro_enabled_presentation")
        if returncode == EXIT_BROKEN_SLIDES:
            raise AssetCorrupt("broken_slide_reference")

        raise StorageUnavailable("Document verification service failed")
