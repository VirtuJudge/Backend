import uuid
from datetime import UTC, datetime

from sqlalchemy import UUID, insert, select ,update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.session_consent_repository import SessionConsentRepository
from app.domain.session_workflow.entities.session_consent import SessionParticipantConsent
from app.infrastructure.persistence.configurations.session_workflow.sessionConsentConfiguration import (
    SessionConsentModel,
)
from app.infrastructure.persistence.mappers.session_practice.session_consent_mapper import (
    to_domain,
    to_model,
)
class SqlAlchemySessionConsentRepository(SessionConsentRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        consent: SessionParticipantConsent,
    ) -> SessionParticipantConsent:

        model = to_model(consent)

        self._session.add(model)
        await self._session.flush()

        return to_domain(model)

    async def get_for_participant(
        self,
        session_id: UUID,
        participant_id: UUID,
    ) -> SessionParticipantConsent | None:

        stmt = select(SessionConsentModel).where(
            SessionConsentModel.session_id == session_id,
            SessionConsentModel.participant_id == participant_id,
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_for_session(
        self,
        session_id: UUID,
    ) -> list[SessionParticipantConsent]:

        stmt = select(SessionConsentModel).where(
            SessionConsentModel.session_id == session_id
        )

        result = await self._session.execute(stmt)
        models = result.scalars().all()

        return [to_domain(model) for model in models]

    async def has_current_consent(
        self,
        session_id: UUID,
        participant_id: UUID,
        policy_version: str,
    ) -> bool:

        stmt = select(SessionConsentModel.id).where(
            SessionConsentModel.session_id == session_id,
            SessionConsentModel.participant_id == participant_id,
            SessionConsentModel.policy_version == policy_version,
            SessionConsentModel.revoked_at.is_(None),
        )

        result = await self._session.execute(stmt)

        return result.scalar_one_or_none() is not None

    async def revoke(
        self,
        consent_id: UUID,
    ) -> None:

        stmt = (
            update(SessionConsentModel)
            .where(SessionConsentModel.id == consent_id)
            .values(revoked_at=datetime.now(UTC))
        )

        await self._session.execute(stmt)
        await self._session.flush()