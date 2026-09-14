from uuid import UUID

from app.application.ports.session_practice.analysis_attempt_repository import (
    AnalysisAttemptRepository,
)
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.exceptions import StaleEntityVersion


class FakeAnalysisAttemptRepository(AnalysisAttemptRepository):
    def __init__(self, initial_attempts: list[AnalysisAttempt] | None = None) -> None:
        self.attempts: dict[UUID, AnalysisAttempt] = {a.id: a for a in (initial_attempts or [])}

    async def get_by_id(self, attempt_id: UUID) -> AnalysisAttempt | None:
        return self.attempts.get(attempt_id)

    async def get_latest(self, session_id: UUID) -> AnalysisAttempt | None:
        matching = [a for a in self.attempts.values() if a.session_id == session_id]
        if not matching:
            return None
        return max(matching, key=lambda a: a.attempt_number)

    async def get_active(self, session_id: UUID) -> AnalysisAttempt | None:
        matching = [
            a
            for a in self.attempts.values()
            if a.session_id == session_id
            and a.status in (AnalysisAttemptStatus.QUEUED, AnalysisAttemptStatus.RUNNING)
        ]
        if not matching:
            return None
        return max(matching, key=lambda a: a.attempt_number)

    async def get_current_by_session_id(self, session_id: UUID) -> AnalysisAttempt | None:
        active = await self.get_active(session_id)
        if active is not None:
            return active
        return await self.get_latest(session_id)

    async def get_by_number(self, session_id: UUID, attempt_number: int) -> AnalysisAttempt | None:
        for a in self.attempts.values():
            if a.session_id == session_id and a.attempt_number == attempt_number:
                return a
        return None

    async def get_next_attempt_number(self, session_id: UUID) -> int:
        matching = [a.attempt_number for a in self.attempts.values() if a.session_id == session_id]
        return max(matching, default=0) + 1

    async def create(self, attempt: AnalysisAttempt) -> AnalysisAttempt:
        self.attempts[attempt.id] = attempt
        return attempt

    async def update(self, attempt: AnalysisAttempt, expected_version: int) -> AnalysisAttempt:
        existing = self.attempts.get(attempt.id)
        if existing is None or existing.version != expected_version:
            raise StaleEntityVersion("Analysis attempt was modified concurrently.")
        attempt.version += 1
        self.attempts[attempt.id] = attempt
        return attempt

    async def get_by_idempotency_key(
        self, session_id: UUID, idempotency_key: str
    ) -> AnalysisAttempt | None:
        for a in self.attempts.values():
            if a.session_id == session_id and a.idempotency_key == idempotency_key:
                return a
        return None

    async def get_all_by_session_id(
        self,
        session_id: UUID,
        next_cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[AnalysisAttempt], str | None]:
        matching = [a for a in self.attempts.values() if a.session_id == session_id]
        matching.sort(key=lambda a: a.id)
        if next_cursor is not None:
            cur_id = UUID(next_cursor)
            matching = [a for a in matching if a.id > cur_id]
        has_more = len(matching) > limit
        page = matching[:limit]
        next_c = str(page[-1].id) if has_more and page else None
        return page, next_c
