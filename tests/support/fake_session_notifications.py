import asyncio

from app.application.ports.session_notification import (
    PendingSessionNotification,
    ReplayBatch,
    ReplayGapReason,
    SessionNotificationPort,
)
from app.application.session_notification_contracts import BasePersistedNotification


class FakeSessionNotifications(SessionNotificationPort):
    def __init__(self) -> None:
        self._events: dict[str, list[BasePersistedNotification]] = {}
        self._heads: dict[str, int] = {}
        self._trimmed_before: dict[str, int] = {}
        self._condition = asyncio.Condition()

    async def publish(self, notification: PendingSessionNotification) -> BasePersistedNotification:
        async with self._condition:
            session_id = notification.practice_session_id
            sequence = self._heads.get(session_id, 0) + 1
            event = notification.with_sequence(sequence)
            self._events.setdefault(session_id, []).append(event)
            self._heads[session_id] = sequence
            self._condition.notify_all()
            return event

    async def replay(self, practice_session_id: str, after_sequence: int) -> ReplayBatch:
        head = self._heads.get(practice_session_id, 0)
        if after_sequence > head:
            return ReplayBatch((), head, ReplayGapReason.CURSOR_FUTURE)
        trimmed_before = self._trimmed_before.get(practice_session_id, 0)
        if after_sequence < trimmed_before:
            return ReplayBatch((), head, ReplayGapReason.CURSOR_TRIMMED)
        events = tuple(
            event
            for event in self._events.get(practice_session_id, [])
            if event.sequence > after_sequence
        )
        return ReplayBatch(events, head)

    async def wait_for_events(
        self,
        practice_session_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float | None = None,
    ) -> ReplayBatch:
        async with self._condition:
            batch = await self.replay(practice_session_id, after_sequence)
            if batch.events or batch.gap_reason is not None:
                return batch
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self._heads.get(practice_session_id, 0) > after_sequence
                    ),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                return ReplayBatch((), self._heads.get(practice_session_id, 0))
            return await self.replay(practice_session_id, after_sequence)

    def trim_through(self, practice_session_id: str, sequence: int) -> None:
        self._events[practice_session_id] = [
            event
            for event in self._events.get(practice_session_id, [])
            if event.sequence > sequence
        ]
        self._trimmed_before[practice_session_id] = max(
            self._trimmed_before.get(practice_session_id, 0), sequence
        )
