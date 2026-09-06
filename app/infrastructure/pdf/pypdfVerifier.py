import asyncio
import sys
from pathlib import Path

from app.application.interfaces.pdfVerifier import PdfVerifierPort
from app.domain.asset import AssetCorrupt, StorageUnavailable
from app.infrastructure.pdf.pdf_worker import (
    EXIT_CORRUPT,
    EXIT_EMPTY,
    EXIT_ENCRYPTED,
    EXIT_MALFORMED,
    EXIT_OK,
)


class PyPdfVerifier(PdfVerifierPort):
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    async def verify_pdf(self, file_path: Path) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.infrastructure.pdf.pdf_worker",
                str(file_path.resolve()),
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
            raise StorageUnavailable("PDF verification service timed out") from err
        except OSError as err:
            raise StorageUnavailable("Failed to start PDF verification worker") from err

        if returncode == EXIT_OK:
            return
        if returncode == EXIT_MALFORMED:
            raise AssetCorrupt("malformed_pdf")
        if returncode == EXIT_ENCRYPTED:
            raise AssetCorrupt("encrypted_pdf")
        if returncode == EXIT_EMPTY:
            raise AssetCorrupt("empty_pdf")
        if returncode == EXIT_CORRUPT:
            raise AssetCorrupt("corrupt_pdf")

        # Killed by signal (e.g. SIGXCPU, SIGKILL, memory exhaustion) or internal error
        raise StorageUnavailable("PDF verification service failed")
