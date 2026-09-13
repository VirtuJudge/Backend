import asyncio

from httpx import ASGITransport, AsyncClient, Response

from app.main import create_app
from app.settings import Settings


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
        _env_file=None,
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


def test_configured_frontend_origin_receives_cors_headers() -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        cors_allowed_origins="https://app.example.com/, http://localhost:3000",
    )
    application = create_app(settings)

    async def preflight() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                "/api/v1/me",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )

    response = asyncio.run(preflight())

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
    assert response.headers["access-control-allow-credentials"] == "true"
