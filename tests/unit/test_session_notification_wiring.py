from app.infrastructure.redis.session_notifications import RedisSessionNotifications
from app.main import create_app
from app.settings import Settings


def test_application_wires_session_notifications() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))

    assert isinstance(app.state.session_notifications, RedisSessionNotifications)
    assert app.state.settings.session_event_max_events == 1000
    assert app.state.settings.session_event_retention_seconds == 86400
