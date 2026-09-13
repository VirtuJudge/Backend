from app.domain.session_workflow.entities.session_practice import PracticeSession

from ...configurations.session_workflow.session_practice_configuration import (
    PracticeSessionModel,
)


def to_domain(model: PracticeSessionModel) -> PracticeSession:
    return PracticeSession(
        id=model.id,
        name=model.name,
        project_id=model.project_id,
        created_by=model.created_by,
        status=model.status,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        cancelled_at=model.cancelled_at,
        consent_granted=model.consent_granted,
        consent_policy_version=model.consent_policy_version,
        consent_confirmed_by=model.consent_confirmed_by,
        consent_confirmed_at=model.consent_confirmed_at,
        cancelled_by=model.cancelled_by,
        cancellation_reason=model.cancellation_reason,
    )


def to_model(entity: PracticeSession) -> PracticeSessionModel:
    return PracticeSessionModel(
        id=entity.id,
        name=entity.name,
        project_id=entity.project_id,
        created_by=entity.created_by,
        status=entity.status,
        version=entity.version,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        cancelled_at=entity.cancelled_at,
        consent_granted=entity.consent_granted,
        consent_policy_version=entity.consent_policy_version,
        consent_confirmed_by=entity.consent_confirmed_by,
        consent_confirmed_at=entity.consent_confirmed_at,
        cancelled_by=entity.cancelled_by,
        cancellation_reason=entity.cancellation_reason,
    )
