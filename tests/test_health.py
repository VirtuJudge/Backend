import asyncio

from httpx import ASGITransport, AsyncClient, Response

from app.infrastructure.settings import Settings
from app.main import create_app


def test_health_endpoint() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    application = create_app(settings)

    async def get_health() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    response = asyncio.run(get_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_do_not_require_production_credentials() -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        object_storage_access_key=None,
        object_storage_secret_key=None,
        oidc_issuer=None,
        oidc_audience=None,
        ai_worker_shared_secret=None,
        gmail_smtp_username=None,
        gmail_smtp_password=None,
        gmail_from_address=None,
    )

    assert settings.mail_backend == "fake"
