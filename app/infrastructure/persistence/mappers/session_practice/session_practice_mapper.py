# infrastructure/persistence/mappers/practice_session_mapper.py

from app.domain.session_workflow.entities.session_practice import PracticeSession

from ...configurations.session_workflow.sessionPracticeConfiguration import (
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
    )
