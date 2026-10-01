import logging
from datetime import UTC, datetime
from uuid import UUID

from app.application.ai_job_contracts import AIWorkerUpdate
from app.application.ports.ai_queue import AIJobQueuePort
from app.application.ports.erasure import ErasureCache, ErasureQueue, ErasureRepository
from app.application.ports.object_storage import ObjectStoragePort
from app.application.ports.session_notification import (
    PendingSessionNotification,
    SessionNotificationPort,
)
from app.application.session_notification_contracts import NotificationEventName
from app.domain.erasure import Erasure, ErasureConflict, ErasureJobState

logger = logging.getLogger(__name__)


class ErasureWorkflow:
    def __init__(
        self,
        repository: ErasureRepository,
        storage: ObjectStoragePort,
        queue: AIJobQueuePort,
        cancellation: ErasureQueue,
        cache: ErasureCache,
        notifications: SessionNotificationPort | None = None,
    ):
        self.repository = repository
        self.storage = storage
        self.queue = queue
        self.cancellation = cancellation
        self.cache = cache
        self.notifications = notifications

    async def request(
        self,
        scope: str,
        scope_id: UUID,
        actor_id: UUID,
        key: str,
        confirmation: str | None = None,
        *,
        expected_team_id: UUID | None = None,
        expected_project_id: UUID | None = None,
    ) -> Erasure:
        request = await self.repository.accept(
            scope,
            scope_id,
            actor_id,
            key,
            confirmation,
            datetime.now(UTC),
            expected_team_id=expected_team_id,
            expected_project_id=expected_project_id,
        )
        await self._notify(request)
        return request

    async def get(self, request_id: UUID, actor_id: UUID) -> Erasure:
        return await self.repository.get(request_id, actor_id)

    async def job_state(self, job_id: UUID) -> ErasureJobState | None:
        return await self.repository.job_state(job_id)

    async def record_update(self, job_id: UUID, update: AIWorkerUpdate) -> ErasureJobState | None:
        return await self.repository.record_update(job_id, update, datetime.now(UTC))

    async def sweep_retention(self, *, batch_size: int = 100, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        await self.repository.adopt_legacy(now, batch_size)
        candidates = await self.repository.retention_candidates(now, batch_size)
        accepted = 0
        for asset_id in candidates:
            try:
                await self.repository.accept(
                    "asset", asset_id, None, f"retention:{asset_id}", None, now, retention=True
                )
                accepted += 1
            except ErasureConflict:
                continue
        return accepted

    async def run_due(self, *, batch_size: int = 100, lease_seconds: int = 300) -> int:
        processed = 0
        for _ in range(batch_size):
            now = datetime.now(UTC)
            request = await self.repository.claim(now, lease_seconds)
            if request is None:
                break
            try:
                await self._process(request, lease_seconds)
                attempts = max((step.attempts for step in request.steps), default=0)
                delay = min(3600, 30 * 2 ** min(attempts, 7))
                await self.repository.release(request, datetime.now(UTC), delay)
            except ErasureConflict:
                # Another coordinator reclaimed an expired lease. Its durable
                # token owns progress now; this runner must not overwrite it.
                continue
            await self._notify(request)
            if request.deadline_at <= now and request.status != "completed":
                logger.error(
                    "Erasure deadline exceeded",
                    extra={
                        "erasure_request_id": str(request.id),
                        "scope_id": str(request.scope_id),
                    },
                )
            processed += 1
        return processed

    async def _process(self, request: Erasure, lease_seconds: int) -> None:
        for step in request.steps:
            if step.status == "completed":
                continue
            records = objects = 0
            try:
                await self.repository.renew(request, datetime.now(UTC), lease_seconds)
                if step.store == "pending_work":
                    records = await self.cancellation.cancel_jobs(request.inventory["job_ids"])
                elif step.store == "ai_data":
                    message = await self.repository.job_message(request, datetime.now(UTC))
                    if message is not None:
                        await self.queue.enqueue(message)
                        await self.repository.mark_dispatched(request, datetime.now(UTC))
                    return
                elif step.store in ("objects", "generated_pdfs"):
                    if step.store == "objects":
                        if datetime.fromisoformat(
                            request.inventory["upload_deadline"]
                        ) > datetime.now(UTC):
                            return
                        for prefix in request.inventory["object_prefixes"]:
                            await self.repository.renew(request, datetime.now(UTC), lease_seconds)
                            await self.repository.add_objects(
                                request, await self.storage.list_objects(prefix)
                            )
                    for item_id, storage_key in await self.repository.object_items(
                        request, step.store
                    ):
                        await self.repository.renew(request, datetime.now(UTC), lease_seconds)
                        await self.storage.delete_object(storage_key)
                        await self.repository.finish_object(request, item_id)
                        objects += 1
                elif step.store == "redis":
                    if request.origin != "retention":
                        records = await self.cache.delete_sessions(request.inventory["session_ids"])
                elif step.store == "backend_records":
                    records = await self.repository.purge_records(request)
                await self.repository.finish_step(
                    request, step.store, datetime.now(UTC), records=records, objects=objects
                )
                logger.info(
                    "Erasure step completed",
                    extra={
                        "erasure_request_id": str(request.id),
                        "store": step.store,
                        "deleted_records": records,
                        "deleted_objects": objects,
                    },
                )
            except ErasureConflict:
                raise
            except Exception:
                await self.repository.finish_step(
                    request, step.store, datetime.now(UTC), failure=f"{step.store}_unavailable"
                )
                logger.warning(
                    "Erasure step failed",
                    extra={"erasure_request_id": str(request.id), "store": step.store},
                )
                return

    async def _notify(self, request: Erasure) -> None:
        if (
            self.notifications is None
            or request.origin == "retention"
            or request.status == "completed"
            or any(step.store == "redis" and step.status == "completed" for step in request.steps)
        ):
            return
        for session_id in request.inventory.get("session_ids", []):
            try:
                await self.notifications.publish(
                    PendingSessionNotification(
                        event_name=NotificationEventName.ERASURE_UPDATED,
                        practice_session_id=session_id,
                        occurred_at=datetime.now(UTC),
                        trace_id=str(request.id),
                        payload={
                            "erasure_request_id": str(request.id),
                            "scope": request.scope,
                            "status": request.status,
                        },
                    )
                )
            except Exception:
                logger.warning(
                    "Erasure notification unavailable",
                    extra={"erasure_request_id": str(request.id)},
                )
