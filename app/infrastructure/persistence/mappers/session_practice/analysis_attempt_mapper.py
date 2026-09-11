from app.domain.session_workflow.entities.analysis_attempt import (
    AnalysisAttempt,
)
from app.infrastructure.persistence.configurations.session_workflow.analysisAttemptConfiguration import (
    AnalysisAttemptModel,
)


def to_domain(model: AnalysisAttemptModel) -> AnalysisAttempt:
    return AnalysisAttempt(
        id=model.id,
        session_id=model.session_id,
        manifest_id=model.manifest_id,
        attempt_number=model.attempt_number,
        status=model.status,
        failure_code=model.failure_code,
        failure_message=model.failure_message,
        created_at=model.created_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        failed_at=model.failed_at,
        cancelled_at=model.cancelled_at,
    )


def to_model(entity: AnalysisAttempt) -> AnalysisAttemptModel:
    return AnalysisAttemptModel(
        id=entity.id,
        session_id=entity.session_id,
        manifest_id=entity.manifest_id,
        attempt_number=entity.attempt_number,
        status=entity.status,
        failure_code=entity.failure_code,
        failure_message=entity.failure_message,
        created_at=entity.created_at,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        failed_at=entity.failed_at,
        cancelled_at=entity.cancelled_at,
    )