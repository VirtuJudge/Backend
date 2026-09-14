from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.session_event_stream import stream_live_session_events
from app.application.ports.session_notification import PendingSessionNotification
from app.application.session_notification_contracts import NotificationEventName
from tests.support.fake_session_notifications import FakeSessionNotifications


class ConnectedRequest:
    class State:
        correlation_id = "trace-request"

    state = State()

    async def is_disconnected(self) -> bool:
        return False


def pending(session_id: str) -> PendingSessionNotification:
    return PendingSessionNotification(
        event_name=NotificationEventName.PRACTICE_SESSION_UPDATED,
        practice_session_id=session_id,
        occurred_at=datetime.now(UTC),
        trace_id="trace-test",
        payload={"version": 1, "state": "ready"},
    )


@pytest.mark.anyio
async def test_live_stream_formats_events() -> None:
    notifications = FakeSessionNotifications()
    session_id = str(uuid4())
    event = await notifications.publish(pending(session_id))
    stream = stream_live_session_events(
        ConnectedRequest(),  # type: ignore[arg-type]
        notifications,
        session_id,
        0,
    )

    assert await anext(stream) == event.to_sse_frame()
    await stream.aclose()


@pytest.mark.anyio
async def test_live_stream_emits_heartbeat_without_allocating_sequence() -> None:
    notifications = FakeSessionNotifications()
    session_id = str(uuid4())
    stream = stream_live_session_events(
        ConnectedRequest(),  # type: ignore[arg-type]
        notifications,
        session_id,
        0,
        heartbeat_seconds=0.001,
    )

    assert await anext(stream) == ": heartbeat\n\n"
    assert (await notifications.replay(session_id, 0)).current_sequence == 0
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_replays_after_last_event_id() -> None:
    notifications = FakeSessionNotifications()
    session_id = str(uuid4())
    await notifications.publish(pending(session_id))
    second = await notifications.publish(pending(session_id))
    stream = stream_live_session_events(
        ConnectedRequest(),  # type: ignore[arg-type]
        notifications,
        session_id,
        1,
    )

    assert await anext(stream) == second.to_sse_frame()
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_emits_resync_for_missing_or_unavailable_cursor() -> None:
    notifications = FakeSessionNotifications()
    session_id = str(uuid4())
    await notifications.publish(pending(session_id))

    missing_cursor_stream = stream_live_session_events(
        ConnectedRequest(),  # type: ignore[arg-type]
        notifications,
        session_id,
        None,
    )
    missing_frame = await anext(missing_cursor_stream)
    assert "event: practice_session.resync_required.v1" in missing_frame
    assert '\"reason\":\"cursor_missing\"' in missing_frame
    await missing_cursor_stream.aclose()

    future_cursor_stream = stream_live_session_events(
        ConnectedRequest(),  # type: ignore[arg-type]
        notifications,
        session_id,
        99,
    )
    future_frame = await anext(future_cursor_stream)
    assert '\"reason\":\"cursor_future\"' in future_frame
    await future_cursor_stream.aclose()
