from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.speaker_mapping_repository import (
    SpeakerMappingRepository,
)
from app.domain.session_workflow.entities.speaker_mapping import (
    SpeakerMapping,
)
from app.infrastructure.persistence.mappers.session_practice.speaker_mapping_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.speakerMappingConfiguration import (
    SpeakerMappingModel,
)


class SqlAlchemySpeakerMappingRepository(SpeakerMappingRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        mapping_id: UUID,
    ) -> SpeakerMapping | None:

        stmt = select(SpeakerMappingModel).where(SpeakerMappingModel.id == mapping_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> list[SpeakerMapping]:

        stmt = select(SpeakerMappingModel).where(SpeakerMappingModel.attempt_id == attempt_id)

        result = await self._session.execute(stmt)

        return [to_domain(model) for model in result.scalars().all()]

    async def get_by_speaker_label(
        self,
        attempt_id: UUID,
        speaker_label: str,
    ) -> SpeakerMapping | None:

        stmt = select(SpeakerMappingModel).where(
            SpeakerMappingModel.attempt_id == attempt_id,
            SpeakerMappingModel.speaker_label == speaker_label,
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def create(
        self,
        mapping: SpeakerMapping,
    ) -> SpeakerMapping:

        model = to_model(mapping)

        self._session.add(model)
        await self._session.flush()

        return to_domain(model)

    async def update(
        self,
        mapping: SpeakerMapping,
    ) -> SpeakerMapping:

        stmt = (
            update(SpeakerMappingModel)
            .where(SpeakerMappingModel.id == mapping.id)
            .values(
                member_id=mapping.member_id,
                mapped_by=mapping.mapped_by,
                mapped_at=mapping.mapped_at,
            )
        )

        await self._session.execute(stmt)
        await self._session.flush()

        return mapping
