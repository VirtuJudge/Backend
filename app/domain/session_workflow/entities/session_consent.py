from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.consent_status import ConsentStatus

class SessionParticipantConsent:
    id: UUID
    session_id: UUID
    participant_id: UUID

    policy_version: str

    actor_id: UUID
    accepted_at: datetime

    revoked_at: datetime | None
    status: ConsentStatus