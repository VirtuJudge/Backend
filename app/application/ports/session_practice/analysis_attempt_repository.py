from typing import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt


class AnalysisAttemptRepository(Protocol):
    async def get_by_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisAttempt | None: ...

    async def get_latest(
        self,
        session_id: UUID,
    ) -> AnalysisAttempt | None: ...

    async def get_active(
        self,
        session_id: UUID,
    ) -> AnalysisAttempt | None: ...

    async def get_by_number(
        self,
        session_id: UUID,
        attempt_number: int,
    ) -> AnalysisAttempt | None: ...

    async def get_next_attempt_number(
        self,
        session_id: UUID,
    ) -> int: ...

    async def create(
        self,
        attempt: AnalysisAttempt,
    ) -> AnalysisAttempt: ...

    async def update(
        self,
        attempt: AnalysisAttempt,
        expected_version: int,
    ) -> AnalysisAttempt: ...

    async def get_by_idempotency_key(
        self,
        session_id: UUID,
        idempotency_key: str,
    ) -> AnalysisAttempt | None: ...

    async def get_all_by_session_id(
        self,
        session_id: UUID,
        next_cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[AnalysisAttempt], str | None]: ...
