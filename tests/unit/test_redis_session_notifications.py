import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.application.ports.session_notification import (
    PendingSessionNotification,
    ReplayGapReason,
)
from app.application.session_notification_contracts import NotificationEventName
from app.infrastructure.redis.session_notifications import RedisSessionNotifications


def pending(session_id: str, version: int = 1) -> PendingSessionNotification:
    return PendingSessionNotification(
        event_name=NotificationEventName.PRACTICE_SESSION_UPDATED,
        practice_session_id=session_id,
        occurred_at=datetime.now(UTC),
        trace_id="trace-test",
        payload={"version": version, "state": "ready"},
    )


@pytest.mark.anyio
async def test_publish_is_atomic_ordered_and_isolated() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisSessionNotifications(redis, max_events=100, retention_seconds=60)
    session_id = str(uuid4())

    published = await asyncio.gather(*(store.publish(pending(session_id)) for _ in range(10)))
    other = await store.publish(pending(str(uuid4())))

    assert sorted(event.sequence for event in published) == list(range(1, 11))
    assert other.sequence == 1
    assert [event.sequence for event in (await store.replay(session_id, 0)).events] == list(
        range(1, 11)
    )


@pytest.mark.anyio
async def test_replay_reports_trimmed_missing_future_and_expired() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisSessionNotifications(redis, max_events=100, retention_seconds=60)
    session_id = str(uuid4())
    for version in range(1, 4):
        await store.publish(pending(session_id, version))
    _, stream_key = store._keys(session_id)

    await redis.xdel(stream_key, "1-0")
    assert (await store.replay(session_id, 0)).gap_reason == ReplayGapReason.CURSOR_TRIMMED
    await redis.xdel(stream_key, "2-0")
    assert (await store.replay(session_id, 2)).gap_reason == ReplayGapReason.CURSOR_MISSING
    assert (await store.replay(session_id, 4)).gap_reason == ReplayGapReason.CURSOR_FUTURE
    await redis.delete(stream_key)
    assert (await store.replay(session_id, 3)).gap_reason == ReplayGapReason.CURSOR_EXPIRED


@pytest.mark.anyio
async def test_wait_returns_live_events_and_times_out() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisSessionNotifications(redis, max_events=100, retention_seconds=60)
    session_id = str(uuid4())
    waiter = asyncio.create_task(store.wait_for_events(session_id, 0, timeout_seconds=1))
    await asyncio.sleep(0)
    await store.publish(pending(session_id))

    assert [event.sequence for event in (await waiter).events] == [1]
    assert not (await store.wait_for_events(session_id, 1, timeout_seconds=0.001)).events
