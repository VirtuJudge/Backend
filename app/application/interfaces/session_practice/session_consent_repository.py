

from asyncio import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.session_consent import SessionParticipantConsent


class SessionConsentRepository(Protocol):

    async def create(
        self,
        consent: SessionParticipantConsent,
    ) -> SessionParticipantConsent:
        ...

    async def get_for_participant(
        self,
        session_id: UUID,
        participant_id: UUID,
    ) -> SessionParticipantConsent | None:
        ...

    async def get_for_session(
        self,
        session_id: UUID,
    ) -> list[SessionParticipantConsent]:
        ...

    async def has_current_consent(
        self,
        session_id: UUID,
        participant_id: UUID,
        policy_version: str,
    ) -> bool:
        ...

    async def revoke(
        self,
        consent_id: UUID,
    ) -> None:
        ...