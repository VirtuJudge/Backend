from typing import cast

from fastapi import HTTPException, Request, status

from app.application.ports.session_notification import SessionNotificationPort


def get_session_notifications(request: Request) -> SessionNotificationPort:
    return cast(SessionNotificationPort, request.app.state.session_notifications)


def reject_access_token_query(request: Request) -> None:
    if "access_token" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
        )
