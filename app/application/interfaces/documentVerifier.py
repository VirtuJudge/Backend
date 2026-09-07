from abc import ABC, abstractmethod
from pathlib import Path


class DocumentVerifierPort(ABC):
    @abstractmethod
    async def verify_document(self, file_path: Path, media_type: str) -> None:
        pass
