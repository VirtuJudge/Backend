from uuid import UUID

from app.application.interfaces.session_practice.session_consent_repository import SessionConsentRepository
from app.domain.session_workflow.entities.session_consent import SessionParticipantConsent

class ConsentPolicyService:
    def __init__(
        self,
        consent_repository: SessionConsentRepository,
    ) -> None:
        self._consent_repository = consent_repository

    async def record_consent(
        self,
        session_id: UUID,
        participant_id: UUID,
        actor_id: UUID,
        policy_version: str,
    ) -> SessionParticipantConsent:
        ...

    async def has_current_consent(
        self,
        session_id: UUID,
        participant_id: UUID,
        current_policy_version: str,
    ) -> bool:
        ...

    async def revoke_consent(
        self,
        session_id: UUID,
        participant_id: UUID,
    ) -> None:
        ...