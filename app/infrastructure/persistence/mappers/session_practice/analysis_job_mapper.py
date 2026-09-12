from app.domain.session_workflow.entities.analysis_job import AnalysisJob

from ...configurations.session_workflow.analysisJobConfiguration import (
    AnalysisJobModel,
)


def to_domain(model: AnalysisJobModel) -> AnalysisJob:
    return AnalysisJob(
        id=model.id,
        attempt_id=model.attempt_id,
        status=model.status,
        correlation_id=model.correlation_id,
        retry_count=model.retry_count,
        last_error=model.last_error,
        created_at=model.created_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
    )


def to_model(entity: AnalysisJob) -> AnalysisJobModel:
    return AnalysisJobModel(
        id=entity.id,
        attempt_id=entity.attempt_id,
        status=entity.status,
        correlation_id=entity.correlation_id,
        retry_count=entity.retry_count,
        last_error=entity.last_error,
        created_at=entity.created_at,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
    )
