from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.session_notification_contracts import (
    BasePersistedNotification,
    NotificationEventName,
    parse_notification,
)


class ReplayGapReason(StrEnum):
    CURSOR_MISSING = "cursor_missing"
    CURSOR_TRIMMED = "cursor_trimmed"
    CURSOR_EXPIRED = "cursor_expired"
    CURSOR_FUTURE = "cursor_future"


class PendingSessionNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: NotificationEventName
    practice_session_id: str = Field(min_length=1)
    occurred_at: datetime
    trace_id: str = Field(min_length=1)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_public_payload(self) -> "PendingSessionNotification":
        reserved = {
            "event",
            "event_name",
            "sequence",
            "practice_session_id",
            "occurred_at",
            "trace_id",
        }
        if reserved.intersection(self.payload):
            raise ValueError("Notification payload cannot override envelope fields")
        if self.event_name == NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED:
            raise ValueError("Resync control frames are not persisted")
        parse_notification(
            {
                "event_name": self.event_name.value,
                "sequence": 1,
                "practice_session_id": self.practice_session_id,
                "occurred_at": self.occurred_at,
                "trace_id": self.trace_id,
                **self.payload,
            }
        )
        return self

    def with_sequence(self, sequence: int) -> BasePersistedNotification:
        notification = parse_notification(
            {
                "event_name": self.event_name.value,
                "sequence": sequence,
                "practice_session_id": self.practice_session_id,
                "occurred_at": self.occurred_at,
                "trace_id": self.trace_id,
                **self.payload,
            }
        )
        if not isinstance(notification, BasePersistedNotification):
            raise ValueError("Only persisted notifications can be published")
        return notification


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    events: tuple[BasePersistedNotification, ...]
    current_sequence: int
    gap_reason: ReplayGapReason | None = None


class SessionNotificationPort(Protocol):
    async def publish(
        self, notification: PendingSessionNotification
    ) -> BasePersistedNotification: ...

    async def replay(self, practice_session_id: str, after_sequence: int) -> ReplayBatch: ...

    async def wait_for_events(
        self,
        practice_session_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float | None = None,
    ) -> ReplayBatch: ...
