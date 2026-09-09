from abc import ABC, abstractmethod
from pathlib import Path


class MediaVerifierPort(ABC):
    @abstractmethod
    async def verify_media(self, file_path: Path, media_type: str, kind: str) -> int:
        pass
