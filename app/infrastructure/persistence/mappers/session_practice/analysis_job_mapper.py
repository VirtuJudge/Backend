from app.domain.session_workflow.entities.analysis_job import AnalysisJob

from ...configurations.session_workflow.analysis_job_configuration import (
    AnalysisJobModel,
)


def to_domain(model: AnalysisJobModel) -> AnalysisJob:
    return AnalysisJob(
        id=model.id,
        practice_session_id=model.practice_session_id,
        attempt_id=model.attempt_id,
        analysis_attempt=model.analysis_attempt,
        job_type=model.job_type,
        status=model.status,
        correlation_id=model.correlation_id,
        last_update_sequence=model.last_update_sequence,
        payload_version=model.payload_version,
        attempts=model.attempts,
        cancel_requested=model.cancel_requested,
        retry_count=model.retry_count,
        last_error=model.last_error,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        payload=model.payload,
        queued_at=model.queued_at,
        next_dispatch_at=model.next_dispatch_at,
        dispatch_retry_count=model.dispatch_retry_count,
        last_dispatch_error_category=model.last_dispatch_error_category,
        completed_result=model.completed_result,
        answer_id=model.answer_id,
    )


def to_model(entity: AnalysisJob) -> AnalysisJobModel:
    return AnalysisJobModel(
        id=entity.id,
        practice_session_id=entity.practice_session_id,
        attempt_id=entity.attempt_id,
        analysis_attempt=entity.analysis_attempt,
        job_type=entity.job_type,
        status=entity.status,
        correlation_id=entity.correlation_id,
        last_update_sequence=entity.last_update_sequence,
        payload_version=entity.payload_version,
        attempts=entity.attempts,
        cancel_requested=entity.cancel_requested,
        retry_count=entity.retry_count,
        last_error=entity.last_error,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        payload=entity.payload,
        queued_at=entity.queued_at,
        next_dispatch_at=entity.next_dispatch_at,
        dispatch_retry_count=entity.dispatch_retry_count,
        last_dispatch_error_category=entity.last_dispatch_error_category,
        completed_result=entity.completed_result,
        answer_id=entity.answer_id,
    )
