from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.ai_job_contracts import AIJobQueueMessage, AIJobType
from app.application.ports.ai_queue import (
    AIJobQueueAccepted,
    AIJobQueueEnvelope,
    AIJobQueuePort,
    AIQueueTemporaryFailure,
)


class FakeAIJobQueue(AIJobQueuePort):
    def __init__(self) -> None:
        self.published_messages: list[AIJobQueueEnvelope] = []
        self.simulate_failure: bool = False
        self.transient_failure: bool = False
        self.failures_remaining: int = 0
        self.failure_to_raise: Exception | None = None
        self.failure_predicate: Callable[[AIJobQueueEnvelope], bool] | None = None

    def _should_fail(self, envelope: AIJobQueueEnvelope) -> bool:
        if self.simulate_failure or self.transient_failure:
            return True
        if self.failures_remaining > 0:
            return True
        return bool(self.failure_predicate is not None and self.failure_predicate(envelope))

    async def enqueue(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        target = message if message is not None else envelope
        if target is None:
            raise ValueError("Must provide message or envelope")

        if isinstance(target, dict):
            target = AIJobQueueMessage.model_validate(target)

        if self._should_fail(target):
            if self.failures_remaining > 0:
                self.failures_remaining -= 1
            if self.failure_to_raise is not None:
                raise self.failure_to_raise
            raise AIQueueTemporaryFailure("Simulated AI queue temporary failure")

        self.published_messages.append(target)
        now = datetime.now(UTC)
        job_id_str = str(target.job_id)
        trace_id_val = getattr(target, "trace_id", None)
        return AIJobQueueAccepted(
            job_id=job_id_str,
            enqueued_at=now,
            message_id=str(uuid4()),
            trace_id=trace_id_val,
        )

    async def publish(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        return await self.enqueue(message, envelope=envelope)

    @property
    def enqueued_messages(self) -> list[AIJobQueueEnvelope]:
        return self.published_messages

    @property
    def messages(self) -> list[AIJobQueueEnvelope]:
        return self.published_messages

    @property
    def last_message(self) -> AIJobQueueEnvelope | None:
        return self.published_messages[-1] if self.published_messages else None

    @property
    def last_envelope(self) -> AIJobQueueEnvelope | None:
        return self.last_message

    @property
    def count(self) -> int:
        return len(self.published_messages)

    def get_by_job_id(self, job_id: str | UUID) -> AIJobQueueEnvelope | None:
        target_str = str(job_id)
        for msg in self.published_messages:
            if str(getattr(msg, "job_id", None)) == target_str:
                return msg
        return None

    def filter_by_job_type(self, job_type: str | AIJobType) -> list[AIJobQueueEnvelope]:
        expected = job_type.value if isinstance(job_type, AIJobType) else str(job_type)
        return [
            msg
            for msg in self.published_messages
            if str(getattr(msg, "job_type", None)) == expected
        ]

    def get_payloads(self) -> list[Any]:
        return [getattr(msg, "payload", None) for msg in self.published_messages]

    def fail_next(self, count: int = 1, error: Exception | None = None) -> None:
        self.failures_remaining = count
        if error is not None:
            self.failure_to_raise = error

    def fail_if(
        self,
        predicate: Callable[[AIJobQueueEnvelope], bool],
        error: Exception | None = None,
    ) -> None:
        self.failure_predicate = predicate
        if error is not None:
            self.failure_to_raise = error

    def clear(self) -> None:
        self.published_messages.clear()
        self.simulate_failure = False
        self.transient_failure = False
        self.failures_remaining = 0
        self.failure_to_raise = None
        self.failure_predicate = None

    reset = clear


RecordingAIJobQueue = FakeAIJobQueue
FakeAIQueue = FakeAIJobQueue
RecordingAIQueue = FakeAIJobQueue
