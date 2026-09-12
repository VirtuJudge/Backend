from app.domain.session_workflow.entities.analysis_stage import AnalysisStage

from ...configurations.session_workflow.analysisStageConfiguration import (
    AnalysisStageModel,
)


def to_domain(model: AnalysisStageModel) -> AnalysisStage:
    return AnalysisStage(
        id=model.id,
        attempt_id=model.attempt_id,
        stage=model.stage,
        status=model.status,
        progress=model.progress,
        started_at=model.started_at,
        completed_at=model.completed_at,
        error_code=model.error_code,
    )


def to_model(entity: AnalysisStage) -> AnalysisStageModel:
    return AnalysisStageModel(
        id=entity.id,
        attempt_id=entity.attempt_id,
        stage=entity.stage,
        status=entity.status,
        progress=entity.progress,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        error_code=entity.error_code,
    )
