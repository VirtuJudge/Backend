from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.diarization_result_reader import (
    DiarizationResultReader,
)


class DefaultDiarizationResultReader(DiarizationResultReader):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_speaker_labels(self, attempt_id: UUID) -> set[str]:
        return set()
