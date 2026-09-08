from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.domain.user import User
from app.infrastructure.settings import Settings
from app.main import create_app


@pytest.mark.anyio
async def test_get_me_returns_display_name() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)

    user = User(
        id=uuid4(),
        issuer="https://identity.example.com",
        subject="alice-subject",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )

    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(user.id)
    assert data["display_name"] == "alice-subject"
    assert "username" not in data
    assert data["email"] == "alice@example.com"
    assert "created_at" in data


@pytest.mark.anyio
async def test_get_me_handles_optional_email() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)

    user = User(
        id=uuid4(),
        issuer="https://identity.example.com",
        subject="bob-subject",
        email=None,
        created_at=datetime.now(UTC),
    )

    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(user.id)
    assert data["display_name"] == "bob-subject"
    assert "username" not in data
    assert data["email"] is None
    assert "created_at" in data
