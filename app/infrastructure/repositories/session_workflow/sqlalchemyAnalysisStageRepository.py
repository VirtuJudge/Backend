from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.analysis_stage_repository import (
    AnalysisStageRepository,
)
from app.domain.session_workflow.entities.analysis_stage import (
    AnalysisStage,
)
from app.domain.session_workflow.enums.stage_type import StageType
from app.infrastructure.persistence.mappers.session_practice.analysis_stage_mapper import (
    to_domain,
)

from ...persistence.configurations.session_workflow.analysisStageConfiguration import (
    AnalysisStageModel,
)


class SQLAlchemyAnalysisStageRepository(AnalysisStageRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_attempt_and_type(
        self,
        attempt_id: UUID,
        stage: StageType,
    ) -> AnalysisStage | None:
        stmt = select(AnalysisStageModel).where(
            AnalysisStageModel.attempt_id == attempt_id,
            AnalysisStageModel.stage == stage,
        )

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            return None

        return to_domain(model)
