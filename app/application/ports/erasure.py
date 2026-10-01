from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.ai_job_contracts import AIWorkerUpdate, EraseAIDataQueueMessage
from app.domain.erasure import Erasure, ErasureJobState


class ErasureRepository(Protocol):
    async def accept(
        self,
        scope: str,
        scope_id: UUID,
        actor_id: UUID | None,
        key: str,
        confirmation: str | None,
        now: datetime,
        *,
        retention: bool = False,
        expected_team_id: UUID | None = None,
        expected_project_id: UUID | None = None,
    ) -> Erasure: ...

    async def get(self, request_id: UUID, actor_id: UUID) -> Erasure: ...

    async def claim(self, now: datetime, lease_seconds: int) -> Erasure | None: ...

    async def renew(self, request: Erasure, now: datetime, lease_seconds: int) -> None: ...

    async def finish_step(
        self,
        request: Erasure,
        store: str,
        now: datetime,
        *,
        records: int = 0,
        objects: int = 0,
        failure: str | None = None,
    ) -> None: ...

    async def release(self, request: Erasure, now: datetime, retry_seconds: int) -> None: ...

    async def object_items(self, request: Erasure, store: str) -> list[tuple[UUID, str]]: ...

    async def add_objects(self, request: Erasure, keys: list[str]) -> None: ...

    async def finish_object(self, request: Erasure, item_id: UUID) -> None: ...

    async def job_message(
        self, request: Erasure, now: datetime
    ) -> EraseAIDataQueueMessage | None: ...

    async def mark_dispatched(self, request: Erasure, now: datetime) -> None: ...

    async def job_state(self, job_id: UUID) -> ErasureJobState | None: ...

    async def record_update(
        self,
        job_id: UUID,
        update: AIWorkerUpdate,
        now: datetime,
    ) -> ErasureJobState | None: ...

    async def purge_records(self, request: Erasure) -> int: ...

    async def retention_candidates(self, now: datetime, limit: int) -> list[UUID]: ...

    async def adopt_legacy(self, now: datetime, limit: int) -> int: ...


class ErasureCache(Protocol):
    async def delete_sessions(self, session_ids: list[str]) -> int: ...


class ErasureQueue(Protocol):
    async def cancel_jobs(self, job_ids: list[str]) -> int: ...
