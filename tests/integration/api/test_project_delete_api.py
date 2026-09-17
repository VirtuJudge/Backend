from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_project_service
from app.application.services.project_service import (
    ProjectConfirmationRequired,
    ProjectForbidden,
    ProjectNotFound,
    ProjectService,
)
from app.domain.user import User
from app.main import create_app
from app.settings import Settings


def _create_user() -> User:
    return User(
        id=uuid4(),
        issuer="https://identity.example.com",
        subject="test-user",
        email="test@example.com",
        created_at=datetime.now(UTC),
        display_name="Test User",
    )


@pytest.mark.anyio
async def test_delete_project_by_owner_succeeds() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.return_value = None

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/projects/{project_id}")

    assert response.status_code == 204
    assert not response.content
    mock_service.delete.assert_awaited_once_with(project_id, user.id, confirmation=None)


@pytest.mark.anyio
async def test_delete_project_with_matching_confirmation_succeeds() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.return_value = None

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE",
            f"/api/v1/projects/{project_id}",
            json={"confirmation": "Awesome Project"},
        )

    assert response.status_code == 204
    mock_service.delete.assert_awaited_once_with(
        project_id, user.id, confirmation="Awesome Project"
    )


@pytest.mark.anyio
async def test_delete_project_by_member_forbidden() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.side_effect = ProjectForbidden

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/projects/{project_id}")

    assert response.status_code == 403
    assert response.json()["detail"] == "project_forbidden"


@pytest.mark.anyio
async def test_delete_project_by_outsider_or_not_found() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.side_effect = ProjectNotFound

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/projects/{project_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "project_not_found"


@pytest.mark.anyio
async def test_delete_project_confirmation_mismatch_conflict() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.side_effect = ProjectConfirmationRequired

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE",
            f"/api/v1/projects/{project_id}",
            json={"confirmation": "Wrong Project Name"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "confirmation_required"


@pytest.mark.anyio
async def test_delete_project_via_team_path() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    user = _create_user()
    team_id = uuid4()
    project_id = uuid4()

    mock_service = AsyncMock(spec=ProjectService)
    mock_service.delete.return_value = None

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_project_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/teams/{team_id}/projects/{project_id}")

    assert response.status_code == 204
    mock_service.delete.assert_awaited_once_with(project_id, user.id, confirmation=None)
