from typing import cast

from fastapi import Request

from app.application.ports.session_notification import SessionNotificationPort


def get_session_notifications(request: Request) -> SessionNotificationPort:
    return cast(SessionNotificationPort, request.app.state.session_notifications)
