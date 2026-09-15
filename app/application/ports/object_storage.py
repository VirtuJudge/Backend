from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


class ObjectStoragePort(ABC):
    @abstractmethod
    def generate_upload_url(
        self,
        storage_key: str,
        content_type: str,
        content_length: int,
        ttl_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        pass

    @abstractmethod
    def generate_download_url(
        self,
        storage_key: str,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        pass

    @abstractmethod
    async def stream_to_disk(
        self,
        storage_key: str,
        target_path: Path,
        max_bytes: int,
    ) -> tuple[int, str, str]:
        pass

    @abstractmethod
    async def get_object(self, storage_key: str, max_bytes: int) -> bytes:
        pass

    @abstractmethod
    async def delete_object(self, storage_key: str) -> None:
        pass

    @abstractmethod
    async def put_object(self, storage_key: str, data: bytes, content_type: str) -> None:
        pass
