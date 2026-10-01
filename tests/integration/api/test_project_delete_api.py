from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.erasure import get_erasure_workflow
from app.application.erasure_workflow import ErasureWorkflow
from app.domain.erasure import Erasure, ErasureConflict, ErasureForbidden, ErasureNotFound
from app.domain.user import User
from app.main import create_app
from app.settings import Settings


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["project", "practice_session"])
async def test_deletion_returns_erasure_and_location(scope: str) -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        issuer="test",
        subject="owner",
        email="owner@example.com",
        display_name="Owner",
        created_at=now,
    )
    target = uuid4()
    erasure = Erasure(uuid4(), uuid4(), scope, target, user.id, now, now + timedelta(hours=24))
    workflow = AsyncMock(spec=ErasureWorkflow)
    workflow.request.return_value = erasure
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_erasure_workflow] = lambda: workflow
    path = (
        f"/api/v1/projects/{target}"
        if scope == "project"
        else f"/api/v1/practice-sessions/{target}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            "DELETE",
            path,
            headers={"Idempotency-Key": "key"},
            json={"confirmation": "Synthetic project"} if scope == "project" else None,
        )
    assert response.status_code == 202
    assert response.json()["id"] == str(erasure.id)
    assert response.headers["Location"] == f"/api/v1/erasure-requests/{erasure.id}"
    expected = (
        (scope, target, user.id, "key", "Synthetic project")
        if scope == "project"
        else (scope, target, user.id, "key")
    )
    workflow.request.assert_awaited_once_with(*expected)


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["project", "practice_session"])
@pytest.mark.parametrize(
    "error, code",
    [
        (ErasureNotFound(), 404),
        (ErasureForbidden(), 403),
        (ErasureConflict("confirmation_required"), 409),
    ],
)
async def test_deletion_errors_are_safe(scope: str, error: Exception, code: int) -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    user = User(
        id=uuid4(),
        issuer="test",
        subject="owner",
        email="owner@example.com",
        display_name="Owner",
        created_at=datetime.now(UTC),
    )
    workflow = AsyncMock(spec=ErasureWorkflow)
    workflow.request.side_effect = error
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_erasure_workflow] = lambda: workflow
    target = uuid4()
    path = (
        f"/api/v1/projects/{target}"
        if scope == "project"
        else f"/api/v1/practice-sessions/{target}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(path, headers={"Idempotency-Key": "key"})
        missing_key = await client.delete(path)
    assert response.status_code == code
    assert response.headers["content-type"] == "application/problem+json"
    assert missing_key.status_code == 422


@pytest.mark.anyio
async def test_status_response_excludes_internal_deletion_inventory() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        issuer="test",
        subject="owner",
        email="owner@example.com",
        display_name="Owner",
        created_at=now,
    )
    erasure = Erasure(
        uuid4(),
        uuid4(),
        "project",
        uuid4(),
        user.id,
        now,
        now + timedelta(hours=24),
        inventory={"storage_key": "synthetic-private-key"},
    )
    workflow = AsyncMock(spec=ErasureWorkflow)
    workflow.get.return_value = erasure
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_erasure_workflow] = lambda: workflow
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/erasure-requests/{erasure.id}")
    assert response.status_code == 200
    assert "inventory" not in response.json()
    assert "synthetic-private-key" not in response.text
    workflow.get.assert_awaited_once_with(erasure.id, user.id)
