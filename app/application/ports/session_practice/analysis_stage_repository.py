from typing import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.analysis_stage import (
    AnalysisStage,
)
from app.domain.session_workflow.enums.stage_type import StageType


class AnalysisStageRepository(Protocol):
    async def create(
        self,
        stage: AnalysisStage,
    ) -> AnalysisStage: ...

    async def get_by_id(
        self,
        stage_id: UUID,
    ) -> AnalysisStage | None: ...

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> list[AnalysisStage]: ...

    async def get_by_attempt_and_type(
        self,
        attempt_id: UUID,
        stage: StageType,
    ) -> AnalysisStage | None: ...

    async def update(
        self,
        stage: AnalysisStage,
    ) -> AnalysisStage: ...
