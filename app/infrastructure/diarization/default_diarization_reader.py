from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.application.ports.session_practice.diarization_result_reader import (
    DiarizationResultReader,
)
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations.project_configuration import ProjectModel
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
)


class DefaultDiarizationResultReader(DiarizationResultReader):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_speaker_labels(self, attempt_id: UUID) -> set[str]:
        if self._session is None:
            return set()

        newer_attempt = aliased(AnalysisAttemptModel)
        newer_attempt_exists = exists(
            select(newer_attempt.id).where(
                newer_attempt.session_id == AnalysisAttemptModel.session_id,
                newer_attempt.attempt_number > AnalysisAttemptModel.attempt_number,
            )
        )

        stmt = (
            select(AnalysisJobModel.completed_result)
            .join(
                AnalysisAttemptModel,
                AnalysisAttemptModel.id == AnalysisJobModel.attempt_id,
            )
            .join(
                PracticeSessionModel,
                (PracticeSessionModel.id == AnalysisJobModel.practice_session_id)
                & (PracticeSessionModel.id == AnalysisAttemptModel.session_id),
            )
            .join(
                ProjectModel,
                ProjectModel.id == PracticeSessionModel.project_id,
            )
            .where(
                AnalysisJobModel.attempt_id == attempt_id,
                AnalysisJobModel.analysis_attempt == AnalysisAttemptModel.attempt_number,
                AnalysisJobModel.job_type == "analyze_session",
                AnalysisJobModel.status == AnalysisJobStatus.COMPLETED,
                AnalysisJobModel.cancel_requested.is_(False),
                AnalysisAttemptModel.status.not_in(
                    [
                        AnalysisAttemptStatus.FAILED,
                        AnalysisAttemptStatus.CANCELLED,
                    ]
                ),
                PracticeSessionModel.access_revoked_at.is_(None),
                ProjectModel.access_revoked_at.is_(None),
                PracticeSessionModel.status.not_in(
                    [
                        SessionStatus.FAILED,
                        SessionStatus.CANCELLED,
                    ]
                ),
                ~newer_attempt_exists,
            )
        )

        result = await self._session.execute(stmt)
        completed_result = result.scalars().first()
        if not isinstance(completed_result, dict):
            return set()

        raw_labels = completed_result.get("speaker_labels")
        if not isinstance(raw_labels, list):
            return set()

        return {str(label) for label in raw_labels if isinstance(label, str) and label.strip()}
