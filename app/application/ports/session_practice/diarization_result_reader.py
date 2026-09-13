from abc import ABC, abstractmethod
from uuid import UUID


class DiarizationResultReader(ABC):
    @abstractmethod
    async def get_speaker_labels(
        self,
        attempt_id: UUID,
    ) -> set[str]: ...


class NullDiarizationResultReader(DiarizationResultReader):
    async def get_speaker_labels(self, attempt_id: UUID) -> set[str]:
        return set()
