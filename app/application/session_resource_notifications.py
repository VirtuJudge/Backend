import logging
from datetime import datetime

from app.application.ports.session_notification import (
    PendingSessionNotification,
    SessionNotificationPort,
)
from app.application.session_notification_contracts import NotificationEventName

logger = logging.getLogger(__name__)


class SessionResourceNotifications:
    def __init__(self, notifications: SessionNotificationPort) -> None:
        self._notifications = notifications

    async def question_available(
        self,
        *,
        practice_session_id: str,
        qa_round_id: str,
        question_id: str,
        position: int,
        kind: str,
        version: int,
        occurred_at: datetime,
        trace_id: str,
    ) -> None:
        await self._publish(
            PendingSessionNotification(
                event_name=NotificationEventName.QA_QUESTION_AVAILABLE,
                practice_session_id=practice_session_id,
                occurred_at=occurred_at,
                trace_id=trace_id,
                payload={
                    "qa_round_id": qa_round_id,
                    "question_id": question_id,
                    "position": position,
                    "kind": kind,
                    "state": "active",
                    "version": version,
                },
            )
        )

    async def answer_updated(
        self,
        *,
        practice_session_id: str,
        qa_round_id: str,
        question_id: str,
        answer_id: str,
        status: str,
        version: int,
        occurred_at: datetime,
        trace_id: str,
    ) -> None:
        await self._publish(
            PendingSessionNotification(
                event_name=NotificationEventName.QA_ANSWER_UPDATED,
                practice_session_id=practice_session_id,
                occurred_at=occurred_at,
                trace_id=trace_id,
                payload={
                    "qa_round_id": qa_round_id,
                    "question_id": question_id,
                    "answer_id": answer_id,
                    "status": status,
                    "version": version,
                },
            )
        )

    async def report_ready(
        self,
        *,
        practice_session_id: str,
        report_id: str,
        evaluation_id: str,
        version: int,
        occurred_at: datetime,
        trace_id: str,
    ) -> None:
        await self._publish(
            PendingSessionNotification(
                event_name=NotificationEventName.REPORT_READY,
                practice_session_id=practice_session_id,
                occurred_at=occurred_at,
                trace_id=trace_id,
                payload={
                    "report_id": report_id,
                    "evaluation_id": evaluation_id,
                    "status": "ready",
                    "version": version,
                },
            )
        )

    async def _publish(self, notification: PendingSessionNotification) -> None:
        try:
            await self._notifications.publish(notification)
        except Exception as exc:
            logger.warning(
                "Resource notification publication failed for session %s: %s",
                notification.practice_session_id,
                type(exc).__name__,
            )
