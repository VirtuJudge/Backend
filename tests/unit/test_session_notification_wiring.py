import pytest
from fastapi import HTTPException, Request

from app.api.dependencies.session_notifications import reject_access_token_query
from app.infrastructure.redis.session_notifications import RedisSessionNotifications
from app.main import create_app
from app.settings import Settings


def test_application_wires_session_notifications() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))

    assert isinstance(app.state.session_notifications, RedisSessionNotifications)
    assert app.state.settings.session_event_max_events == 1000
    assert app.state.settings.session_event_retention_seconds == 86400


def test_query_access_token_is_rejected_before_streaming() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/practice-sessions/session-id/events",
            "query_string": b"access_token=secret",
            "headers": [],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        reject_access_token_query(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_token"
