from abc import ABC, abstractmethod
from pathlib import Path


class PdfVerifierPort(ABC):
    @abstractmethod
    async def verify_pdf(self, file_path: Path) -> None:
        pass
