from app.domain.session_workflow.entities.session_consent import (
    SessionParticipantConsent,
)
from app.infrastructure.persistence.configurations.session_workflow.sessionConsentConfiguration import (
    SessionConsentModel,
)


def to_domain(model: SessionConsentModel) -> SessionParticipantConsent:
    return SessionParticipantConsent(
        id=model.id,
        session_id=model.session_id,
        participant_id=model.participant_id,
        policy_version=model.policy_version,
        accepted_at=model.accepted_at,
        actor_id=model.actor_id,
        revoked_at=model.revoked_at,
    )


def to_model(
    entity: SessionParticipantConsent,
) -> SessionConsentModel:
    return SessionConsentModel(
        id=entity.id,
        session_id=entity.session_id,
        participant_id=entity.participant_id,
        policy_version=entity.policy_version,
        accepted_at=entity.accepted_at,
        actor_id=entity.actor_id,
        revoked_at=entity.revoked_at,
    )