from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeAlias

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AnalyzeAnswerQueueMessage,
    AnalyzeSessionQueueMessage,
    BaseAIJobQueueMessage,
    EraseAIDataQueueMessage,
    GenerateReportQueueMessage,
    TypedAIJobQueueMessage,
)

AIJobQueueEnvelope: TypeAlias = (
    AIJobQueueMessage
    | AnalyzeSessionQueueMessage
    | AnalyzeAnswerQueueMessage
    | GenerateReportQueueMessage
    | EraseAIDataQueueMessage
    | BaseAIJobQueueMessage[Any]
    | TypedAIJobQueueMessage
)


@dataclass(frozen=True, slots=True)
class AIJobQueueAccepted:
    job_id: str
    enqueued_at: datetime
    message_id: str | None = None
    trace_id: str | None = None


AIQueueAccepted = AIJobQueueAccepted


class AIQueueError(Exception):
    pass


class AIQueueTemporaryFailure(AIQueueError):
    def __init__(
        self,
        message: str = "AI queue is temporarily unavailable",
        *,
        retry_after_seconds: float | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.cause = cause


AIJobQueueTemporaryFailure = AIQueueTemporaryFailure
AIQueueUnavailable = AIQueueTemporaryFailure
AIJobQueueUnavailable = AIQueueTemporaryFailure
AIJobQueueError = AIQueueError


class AIJobQueuePort(ABC):
    @abstractmethod
    async def enqueue(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        pass

    async def publish(
        self,
        message: AIJobQueueEnvelope | None = None,
        *,
        envelope: AIJobQueueEnvelope | None = None,
    ) -> AIJobQueueAccepted:
        return await self.enqueue(message, envelope=envelope)


AIQueuePort = AIJobQueuePort
