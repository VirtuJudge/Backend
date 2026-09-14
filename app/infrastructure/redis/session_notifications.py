import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.application.ports.session_notification import (
    PendingSessionNotification,
    ReplayBatch,
    ReplayGapReason,
    SessionNotificationPort,
)
from app.application.session_notification_contracts import (
    BasePersistedNotification,
    parse_notification,
)


class RedisSessionNotifications(SessionNotificationPort):
    def __init__(self, redis: Redis, *, max_events: int, retention_seconds: int) -> None:
        self._redis = redis
        self._max_events = max_events
        self._retention_seconds = retention_seconds

    @staticmethod
    def _keys(practice_session_id: str) -> tuple[str, str]:
        tag = f"{{{practice_session_id}}}"
        return f"session-events:{tag}:sequence", f"session-events:{tag}:stream"

    async def publish(self, notification: PendingSessionNotification) -> BasePersistedNotification:
        counter_key, stream_key = self._keys(notification.practice_session_id)
        data = notification.model_dump(mode="json", exclude={"event_name", "payload"})
        data.update(notification.payload)

        while True:
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(counter_key)
                    current = await pipeline.get(counter_key)
                    sequence = int(current or 0) + 1
                    pipeline.multi()  # type: ignore[no-untyped-call]
                    pipeline.set(
                        counter_key,
                        sequence,
                        ex=self._retention_seconds * 2,
                    )
                    pipeline.xadd(
                        stream_key,
                        {
                            "event_name": notification.event_name.value,
                            "data": json.dumps(data, separators=(",", ":")),
                        },
                        id=f"{sequence}-0",
                        maxlen=self._max_events,
                        approximate=True,
                    )
                    pipeline.expire(stream_key, self._retention_seconds)
                    await pipeline.execute()
                    return notification.with_sequence(sequence)
                except WatchError:
                    continue

    async def replay(self, practice_session_id: str, after_sequence: int) -> ReplayBatch:
        counter_key, stream_key = self._keys(practice_session_id)
        current = await self._redis.get(counter_key)
        head = int(current or 0)
        if after_sequence > head:
            return ReplayBatch((), head, ReplayGapReason.CURSOR_FUTURE)

        first_entries: Any = await self._redis.xrange(stream_key, count=1)
        if not first_entries:
            gap = ReplayGapReason.CURSOR_EXPIRED if head else None
            return ReplayBatch((), head, gap)

        earliest = self._sequence(first_entries[0][0])
        if after_sequence < earliest - 1:
            return ReplayBatch((), head, ReplayGapReason.CURSOR_TRIMMED)
        if after_sequence and after_sequence < head:
            exact = await self._redis.xrange(
                stream_key,
                min=f"{after_sequence}-0",
                max=f"{after_sequence}-0",
                count=1,
            )
            if not exact:
                return ReplayBatch((), head, ReplayGapReason.CURSOR_MISSING)

        entries: Any = await self._redis.xrange(stream_key, min=f"({after_sequence}-0")
        return ReplayBatch(tuple(self._decode(entry) for entry in entries), head)

    async def wait_for_events(
        self,
        practice_session_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float | None = None,
    ) -> ReplayBatch:
        initial = await self.replay(practice_session_id, after_sequence)
        if initial.events or initial.gap_reason is not None:
            return initial
        _, stream_key = self._keys(practice_session_id)
        block_ms = 0 if timeout_seconds is None else max(1, int(timeout_seconds * 1000))
        response: Any = await self._redis.xread(
            {stream_key: f"{after_sequence}-0"},
            block=block_ms,
        )
        if not response:
            return await self.replay(practice_session_id, after_sequence)
        entries = response[0][1]
        events = tuple(self._decode(entry) for entry in entries)
        return ReplayBatch(events, events[-1].sequence if events else after_sequence)

    @staticmethod
    def _sequence(raw_id: str | bytes | None) -> int:
        if raw_id is None:
            raise ValueError("Redis stream entry is missing an ID")
        value = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        return int(value.split("-", maxsplit=1)[0])

    def _decode(
        self,
        entry: tuple[str | bytes, dict[str | bytes, str | bytes]],
    ) -> BasePersistedNotification:
        raw_id, fields = entry
        normalized = {
            (key.decode() if isinstance(key, bytes) else key): (
                value.decode() if isinstance(value, bytes) else value
            )
            for key, value in fields.items()
        }
        data = json.loads(normalized["data"])
        notification = parse_notification(
            {
                "event_name": normalized["event_name"],
                "sequence": self._sequence(raw_id),
                **data,
            }
        )
        if not isinstance(notification, BasePersistedNotification):
            raise ValueError("Redis stream contained an unpersisted control frame")
        return notification
