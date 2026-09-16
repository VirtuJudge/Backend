from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetUploadIdempotencyModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.project_configuration import ProjectModel
from app.infrastructure.persistence.configurations.project_erasure_request import (
    ProjectErasureRequestModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AIJobModel,
    AnalysisAttemptModel,
    AnalysisStageModel,
    AnswerModel,
    EvaluationModel,
    PracticeSessionModel,
    QARoundModel,
    QuestionModel,
    ReportExportModel,
    ReportModel,
    SessionCommandIdempotencyModel,
    SessionManifestDocumentModel,
    SessionManifestModel,
    SpeakerMappingModel,
)


async def delete_practice_sessions(session: AsyncSession, session_ids: list[UUID]) -> int:
    if not session_ids:
        return 0

    # 1. Report exports and reports
    await session.execute(
        delete(ReportExportModel).where(ReportExportModel.practice_session_id.in_(session_ids))
    )
    await session.execute(
        delete(ReportModel).where(ReportModel.practice_session_id.in_(session_ids))
    )

    # 2. Evaluations
    await session.execute(
        delete(EvaluationModel).where(EvaluationModel.practice_session_id.in_(session_ids))
    )

    # 3. AI Jobs referencing the sessions
    await session.execute(
        delete(AIJobModel).where(AIJobModel.practice_session_id.in_(session_ids))
    )

    # 4. Answers, questions, and QA rounds
    qa_round_subquery = select(QARoundModel.id).where(
        QARoundModel.practice_session_id.in_(session_ids)
    )
    await session.execute(
        delete(AnswerModel).where(AnswerModel.qa_round_id.in_(qa_round_subquery))
    )
    await session.execute(
        delete(QuestionModel).where(QuestionModel.practice_session_id.in_(session_ids))
    )
    await session.execute(
        delete(QARoundModel).where(QARoundModel.practice_session_id.in_(session_ids))
    )

    # 5. Attempts and their children
    attempt_subquery = select(AnalysisAttemptModel.id).where(
        AnalysisAttemptModel.session_id.in_(session_ids)
    )
    await session.execute(
        delete(SpeakerMappingModel).where(SpeakerMappingModel.attempt_id.in_(attempt_subquery))
    )
    await session.execute(
        delete(AnalysisStageModel).where(AnalysisStageModel.attempt_id.in_(attempt_subquery))
    )
    await session.execute(
        delete(AIJobModel).where(AIJobModel.attempt_id.in_(attempt_subquery))
    )

    # 6. Session manifest documents
    manifest_subquery = select(SessionManifestModel.id).where(
        SessionManifestModel.session_id.in_(session_ids)
    )
    await session.execute(
        delete(SessionManifestDocumentModel).where(
            SessionManifestDocumentModel.manifest_id.in_(manifest_subquery)
        )
    )

    # 7. Analysis attempts
    await session.execute(
        delete(AnalysisAttemptModel).where(AnalysisAttemptModel.session_id.in_(session_ids))
    )

    # 8. Session manifests
    await session.execute(
        delete(SessionManifestModel).where(SessionManifestModel.session_id.in_(session_ids))
    )

    # 9. Session command idempotency
    await session.execute(
        delete(SessionCommandIdempotencyModel).where(
            SessionCommandIdempotencyModel.session_id.in_(session_ids)
        )
    )

    # 10. Practice sessions
    result = await session.execute(
        delete(PracticeSessionModel).where(PracticeSessionModel.id.in_(session_ids))
    )
    return int(result.rowcount or 0)


async def delete_project_records(session: AsyncSession, project_id: UUID) -> bool:
    # 1. Delete all practice sessions for this project
    session_ids_result = await session.scalars(
        select(PracticeSessionModel.id).where(PracticeSessionModel.project_id == project_id)
    )
    session_ids = list(session_ids_result.all())
    if session_ids:
        await delete_practice_sessions(session, session_ids)

    # 2. Delete all assets and versions for this project
    asset_ids_subquery = select(AssetModel.id).where(AssetModel.project_id == project_id)
    await session.execute(
        delete(AssetUploadIdempotencyModel).where(
            AssetUploadIdempotencyModel.project_id == project_id
        )
    )
    # Clear current_version_id to avoid potential circular reference constraint
    await session.execute(
        update(AssetModel)
        .where(AssetModel.project_id == project_id)
        .values(current_version_id=None)
    )
    await session.execute(
        delete(AssetVersionModel).where(AssetVersionModel.asset_id.in_(asset_ids_subquery))
    )
    await session.execute(
        delete(AssetModel).where(AssetModel.project_id == project_id)
    )

    # 3. Delete project erasure requests
    await session.execute(
        delete(ProjectErasureRequestModel).where(
            ProjectErasureRequestModel.project_id == project_id
        )
    )

    # 4. Delete the project itself
    result = await session.execute(
        delete(ProjectModel).where(ProjectModel.id == project_id)
    )
    return bool((result.rowcount or 0) > 0)
