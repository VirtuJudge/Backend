from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.session_notification_contracts import NotificationEventName
from app.application.session_resource_notifications import SessionResourceNotifications
from tests.support.fake_session_notifications import FakeSessionNotifications


@pytest.mark.anyio
async def test_publishes_safe_resource_availability_events() -> None:
    notifications = FakeSessionNotifications()
    service = SessionResourceNotifications(notifications)
    session_id = str(uuid4())
    occurred_at = datetime.now(UTC)

    await service.question_available(
        practice_session_id=session_id,
        qa_round_id=str(uuid4()),
        question_id=str(uuid4()),
        position=1,
        kind="primary",
        version=1,
        occurred_at=occurred_at,
        trace_id="trace-test",
    )
    await service.answer_updated(
        practice_session_id=session_id,
        qa_round_id=str(uuid4()),
        question_id=str(uuid4()),
        answer_id=str(uuid4()),
        status="submitted",
        version=2,
        occurred_at=occurred_at,
        trace_id="trace-test",
    )
    await service.report_ready(
        practice_session_id=session_id,
        report_id=str(uuid4()),
        evaluation_id=str(uuid4()),
        version=3,
        occurred_at=occurred_at,
        trace_id="trace-test",
    )

    events = (await notifications.replay(session_id, 0)).events
    assert [event.event_name for event in events] == [
        NotificationEventName.QA_QUESTION_AVAILABLE,
        NotificationEventName.QA_ANSWER_UPDATED,
        NotificationEventName.REPORT_READY,
    ]
    for event in events:
        data = event.model_dump()
        assert "text" not in data
        assert "transcript" not in data
        assert "object_key" not in data


@pytest.mark.anyio
async def test_publication_failure_is_best_effort() -> None:
    class FailingNotifications(FakeSessionNotifications):
        async def publish(self, notification):  # type: ignore[no-untyped-def]
            raise RuntimeError("unavailable")

    service = SessionResourceNotifications(FailingNotifications())
    await service.report_ready(
        practice_session_id=str(uuid4()),
        report_id=str(uuid4()),
        evaluation_id=str(uuid4()),
        version=1,
        occurred_at=datetime.now(UTC),
        trace_id="trace-test",
    )
