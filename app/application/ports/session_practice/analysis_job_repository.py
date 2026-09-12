from typing import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.analysis_job import AnalysisJob


class AnalysisJobRepository(Protocol):
    async def get_by_id(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None: ...

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisJob | None: ...

    async def create(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob: ...

    async def update(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob: ...
