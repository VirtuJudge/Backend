import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.ports.session_notification import (
    PendingSessionNotification,
    ReplayGapReason,
)
from app.application.session_notification_contracts import NotificationEventName
from tests.support.fake_session_notifications import FakeSessionNotifications


def pending(session_id: str) -> PendingSessionNotification:
    return PendingSessionNotification(
        event_name=NotificationEventName.PRACTICE_SESSION_UPDATED,
        practice_session_id=session_id,
        occurred_at=datetime.now(UTC),
        trace_id="trace-test",
        payload={"version": 1, "state": "ready"},
    )


@pytest.mark.anyio
async def test_fake_allocates_ordered_sequences_per_session() -> None:
    fake = FakeSessionNotifications()
    first_session = str(uuid4())
    second_session = str(uuid4())

    first, second = await asyncio.gather(
        fake.publish(pending(first_session)),
        fake.publish(pending(first_session)),
    )
    other = await fake.publish(pending(second_session))

    assert [first.sequence, second.sequence] == [1, 2]
    assert other.sequence == 1
    assert [event.sequence for event in (await fake.replay(first_session, 0)).events] == [1, 2]


@pytest.mark.anyio
async def test_fake_reports_trimmed_and_future_cursors() -> None:
    fake = FakeSessionNotifications()
    session_id = str(uuid4())
    await fake.publish(pending(session_id))
    await fake.publish(pending(session_id))
    fake.trim_through(session_id, 1)

    assert (await fake.replay(session_id, 0)).gap_reason == ReplayGapReason.CURSOR_TRIMMED
    assert (await fake.replay(session_id, 3)).gap_reason == ReplayGapReason.CURSOR_FUTURE
    assert [event.sequence for event in (await fake.replay(session_id, 1)).events] == [2]


@pytest.mark.anyio
async def test_fake_waits_for_live_event_and_times_out_cleanly() -> None:
    fake = FakeSessionNotifications()
    session_id = str(uuid4())
    waiter = asyncio.create_task(
        fake.wait_for_events(session_id, 0, timeout_seconds=1)
    )
    await asyncio.sleep(0)
    await fake.publish(pending(session_id))

    assert [event.sequence for event in (await waiter).events] == [1]
    assert not (await fake.wait_for_events(session_id, 1, timeout_seconds=0.001)).events


@pytest.mark.anyio
async def test_fake_wait_is_cancellable() -> None:
    fake = FakeSessionNotifications()
    waiter = asyncio.create_task(fake.wait_for_events(str(uuid4()), 0))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter


def test_pending_notification_rejects_private_or_unknown_fields() -> None:
    with pytest.raises(ValueError):
        pending(str(uuid4())).model_copy(
            update={"payload": {"version": 1, "state": "ready", "transcript": "private"}}
        ).with_sequence(1)
