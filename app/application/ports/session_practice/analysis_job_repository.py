from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus


class AnalysisJobRepository(Protocol):
    async def get_by_id(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None: ...

    async def get_by_id_for_update(self, job_id: UUID) -> AnalysisJob | None: ...

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisJob | None: ...

    async def get_by_answer_id(self, answer_id: UUID) -> AnalysisJob | None: ...

    async def get_completed_report_by_session_id(
        self,
        session_id: UUID,
    ) -> AnalysisJob | None: ...

    async def create(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob: ...

    async def update(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob: ...

    async def get_eligible_pending_jobs(
        self,
        now: datetime,
        limit: int = 10,
        *,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[AnalysisJob]: ...

    async def recover_stale_inflight_jobs(
        self,
        stale_before: datetime,
        now: datetime,
        limit: int = 10,
    ) -> int: ...

    async def load_eligible_pending_batch(
        self,
        now: datetime,
        limit: int = 10,
        *,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[AnalysisJob]: ...

    async def mark_as_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> bool: ...

    async def change_pending_to_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> AnalysisJob | None: ...

    async def record_dispatch_failure(
        self,
        job_id: UUID,
        now: datetime,
        next_eligible_at: datetime,
        error_category: str,
        error_message: str | None = None,
    ) -> AnalysisJob | None: ...

    async def apply_callback(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> bool: ...

    async def apply_callback_update(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> AnalysisJob | None: ...

    async def get_ancestry_context(
        self,
        job_id: UUID,
    ) -> AIJobAncestryContext | None: ...

    async def load_ancestry_context(
        self,
        job_id: UUID,
    ) -> AIJobAncestryContext | None: ...

    async def get_ancestry_context_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AIJobAncestryContext | None: ...
