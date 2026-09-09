from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.domain.user import User
from app.settings import Settings
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
        display_name="alice-subject",
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
        display_name="bob-subject",
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


@pytest.mark.anyio
async def test_get_me_extracts_display_name_from_token_claims() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)

    class FakeVerifier:
        def verify(self, token: str) -> dict[str, object]:
            return {
                "iss": "https://identity.example.com",
                "sub": "user-sub-123",
                "email": "charlie@example.com",
                "user_metadata": {"display_name": "Charlie Chaplin"},
            }

    app.state.token_verifier = FakeVerifier()

    from app.api.dependencies.services import get_user_service
    from app.application.interfaces.userRepository import UserRepository
    from app.application.services.userService import UserService

    class FakeUserRepository(UserRepository):
        def __init__(self) -> None:
            self.user: User | None = None

        async def get_by_identity(self, issuer: str, subject: str) -> User | None:
            return self.user

        async def get_by_id(self, user_id: object) -> User | None:
            if self.user is not None and self.user.id == user_id:
                return self.user
            return None

        async def create(self, user: User) -> User:
            self.user = user
            return user

    fake_repo = FakeUserRepository()
    user_service = UserService(fake_repo)
    app.dependency_overrides[get_user_service] = lambda: user_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer fake-token"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Charlie Chaplin"
    assert data["email"] == "charlie@example.com"
    assert fake_repo.user is not None
    assert fake_repo.user.display_name == "Charlie Chaplin"
