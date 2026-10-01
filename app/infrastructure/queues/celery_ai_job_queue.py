import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from celery import Celery
from celery.exceptions import CeleryError
from kombu.exceptions import KombuError
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError

from app.application.ai_job_contracts import AIJobQueueMessage
from app.application.ports.ai_queue import (
    AIJobQueueAccepted,
    AIJobQueueEnvelope,
    AIJobQueuePort,
    AIQueueTemporaryFailure,
)
from app.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_WORKER_TASK_NAME = "app.worker.process_job"
DEFAULT_QUEUE_NAME = "ai_jobs"

ALLOWED_ENVELOPE_FIELDS = {
    "schema_version",
    "job_id",
    "job_type",
    "practice_session_id",
    "analysis_attempt",
    "created_at",
    "trace_id",
    "payload",
}


def create_celery_app(
    broker_url: str,
    app_name: str = "virtujudge_backend",
) -> Celery:
    app = Celery(app_name, broker=broker_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_default_queue=DEFAULT_QUEUE_NAME,
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        broker_connection_retry_on_startup=False,
    )
    return app


class CeleryAIJobQueue(AIJobQueuePort):
    def __init__(
        self,
        celery_app: Celery | None = None,
        *,
        broker_url: str | None = None,
        task_name: str = DEFAULT_WORKER_TASK_NAME,
        queue_name: str = DEFAULT_QUEUE_NAME,
        task_options: dict[str, Any] | None = None,
    ) -> None:
        if celery_app is not None:
            self._celery_app = celery_app
        elif broker_url is not None:
            self._celery_app = create_celery_app(broker_url)
        else:
            raise ValueError("Either celery_app or broker_url must be provided")

        self.task_name = task_name
        self.queue_name = queue_name
        self.task_options = dict(task_options or {})

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        celery_app: Celery | None = None,
        task_options: dict[str, Any] | None = None,
    ) -> "CeleryAIJobQueue":
        return cls(
            celery_app=celery_app,
            broker_url=settings.effective_celery_broker_url if celery_app is None else None,
            task_name=settings.ai_worker_task_name,
            queue_name=settings.ai_worker_queue_name,
            task_options=task_options,
        )

    def _build_envelope_dict(self, target: Any) -> dict[str, Any]:
        if isinstance(target, BaseModel):
            raw = target.model_dump(mode="json", exclude_none=True)
        elif isinstance(target, dict):
            model = AIJobQueueMessage.model_validate(target)
            raw = model.model_dump(mode="json", exclude_none=True)
        else:
            raise TypeError(f"Unsupported envelope type: {type(target)}")

        filtered = {k: raw[k] for k in ALLOWED_ENVELOPE_FIELDS if k in raw}
        filtered["schema_version"] = 1
        filtered["job_id"] = str(filtered["job_id"])
        return filtered

    def _publish_sync(
        self,
        job_id: str,
        envelope_dict: dict[str, Any],
    ) -> None:
        options = dict(self.task_options)
        options.setdefault("queue", self.queue_name)
        self._celery_app.send_task(
            name=self.task_name,
            args=[envelope_dict],
            task_id=job_id,
            **options,
        )

    async def enqueue(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        target = message if message is not None else envelope
        if target is None:
            raise ValueError("Must provide message or envelope")

        envelope_dict = self._build_envelope_dict(target)
        job_id = envelope_dict["job_id"]
        trace_id = envelope_dict.get("trace_id")
        job_type = envelope_dict.get("job_type")

        try:
            await asyncio.to_thread(
                self._publish_sync,
                job_id=job_id,
                envelope_dict=envelope_dict,
            )
        except (
            CeleryError,
            KombuError,
            RedisError,
            OSError,
            ConnectionError,
            TimeoutError,
        ) as exc:
            logger.warning(
                "Failed to enqueue AI job to broker",
                extra={
                    "job_id": job_id,
                    "job_type": job_type,
                    "trace_id": trace_id,
                    "error_type": exc.__class__.__name__,
                },
            )
            raise AIQueueTemporaryFailure(
                f"Failed to enqueue AI job '{job_id}' to broker: {exc.__class__.__name__}",
                cause=exc,
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error enqueuing AI job to broker",
                extra={
                    "job_id": job_id,
                    "job_type": job_type,
                    "trace_id": trace_id,
                    "error_type": exc.__class__.__name__,
                },
            )
            raise AIQueueTemporaryFailure(
                f"Failed to enqueue AI job '{job_id}' to broker: {exc.__class__.__name__}",
                cause=exc,
            ) from exc

        logger.info(
            "Enqueued AI job to broker",
            extra={
                "job_id": job_id,
                "job_type": job_type,
                "trace_id": trace_id,
            },
        )

        return AIJobQueueAccepted(
            job_id=job_id,
            enqueued_at=datetime.now(UTC),
            message_id=str(uuid4()),
            trace_id=trace_id,
        )

    async def publish(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        return await self.enqueue(message, envelope=envelope)

    def __repr__(self) -> str:
        return f"CeleryAIJobQueue(task_name={self.task_name!r}, queue_name={self.queue_name!r})"

    async def cancel_jobs(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        targets = set(job_ids)

        def cancel() -> int:
            self._celery_app.control.revoke(job_ids, terminate=False)
            removed = 0
            broker = str(self._celery_app.conf.broker_url)
            with Redis.from_url(broker) as redis:
                for priority in (0, 3, 6, 9):
                    key = (
                        self.queue_name if priority == 0 else f"{self.queue_name}\x06\x16{priority}"
                    )
                    offset = 0
                    matches = []
                    while batch := redis.lrange(key, offset, offset + 99):
                        for raw in batch:
                            message = json.loads(raw)
                            if str(message.get("headers", {}).get("id")) in targets:
                                matches.append(raw)
                        offset += len(batch)
                    for raw in matches:
                        removed += int(redis.lrem(key, 0, raw))
                # Kombu keeps reserved deliveries in a hash until ACK. Remove
                # only revoked target payloads so a disconnected worker cannot
                # later restore their sensitive envelope to the queue.
                for delivery_tag, raw in redis.hscan_iter("unacked", count=100):
                    message = json.loads(raw)[0]
                    if str(message.get("headers", {}).get("id")) in targets:
                        with redis.pipeline() as transaction:
                            transaction.hdel("unacked", delivery_tag)
                            transaction.zrem("unacked_index", delivery_tag)
                            result = transaction.execute()
                        removed += int(result[0])
            inspect = self._celery_app.control.inspect(timeout=2.0)
            for snapshot in (inspect.active(), inspect.reserved(), inspect.scheduled()):
                if snapshot is None:
                    raise AIQueueTemporaryFailure("Worker cancellation supervision unavailable")
                if any(
                    str(task.get("request", task).get("id")) in targets
                    for tasks in (snapshot or {}).values()
                    for task in tasks
                ):
                    raise AIQueueTemporaryFailure("Cancelled jobs are still finishing")
            return removed

        try:
            return await asyncio.to_thread(cancel)
        except AIQueueTemporaryFailure:
            raise
        except Exception as exc:
            raise AIQueueTemporaryFailure("Job cancellation is unavailable") from exc


CeleryAIQueue = CeleryAIJobQueue
