from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.session_event_stream import stream_live_session_events
from app.application.ports.session_notification import PendingSessionNotification
from app.application.session_notification_contracts import NotificationEventName
from tests.support.fake_session_notifications import FakeSessionNotifications


class ConnectedRequest:
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
