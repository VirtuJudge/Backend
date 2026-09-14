import logging
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.dependencies.worker_auth import (
    AuthenticatedWorker,
    require_worker_auth,
)
from app.infrastructure.auth.worker_auth import (
    WorkerAuthConfigurationError,
)
from app.main import create_app
from app.settings import Settings


def _create_test_app(
    secret: str | None = "expected-worker-shared-secret",
) -> tuple[Any, dict[UUID, bool]]:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        ai_worker_shared_secret=SecretStr(secret) if secret else None,
    )
    application = create_app(settings)

    # Simulated AI Job storage and router
    existing_jobs: dict[UUID, bool] = {uuid4(): True}
    router = APIRouter(prefix="/api/v1/internal/worker")
    lookup_called = False

    @router.get("/ai-jobs/{job_id}")
    async def get_worker_ai_job(
        job_id: UUID,
        worker: AuthenticatedWorker = Depends(require_worker_auth),
    ) -> dict[str, Any]:
        nonlocal lookup_called
        lookup_called = True
        exists = job_id in existing_jobs
        if not exists:
            return {"found": False}
        return {"found": True, "job_id": str(job_id), "worker_role": worker.role}

    application.include_router(router)
    return application, existing_jobs


@pytest.mark.anyio
async def test_worker_auth_correct_credentials() -> None:
    app, existing_jobs = _create_test_app("my-worker-secret")
    job_id = next(iter(existing_jobs))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Bearer my-worker-secret"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["job_id"] == str(job_id)
        assert data["worker_role"] == "ai_worker"


@pytest.mark.anyio
async def test_worker_auth_identical_401_problem_details_for_missing_malformed_incorrect() -> None:
    app, existing_jobs = _create_test_app("my-worker-secret")
    job_id = next(iter(existing_jobs))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing credentials
        res_missing = await client.get(f"/api/v1/internal/worker/ai-jobs/{job_id}")

        # 2. Malformed: wrong scheme (Basic)
        res_basic = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )

        # 3. Malformed: empty bearer
        res_empty = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Bearer"},
        )

        # 4. Malformed: bearer with spaces only
        res_spaces = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Bearer    "},
        )

        # 5. Malformed: other scheme (Token)
        res_token = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Token my-worker-secret"},
        )

        # 6. Malformed: no scheme
        res_no_scheme = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "my-worker-secret"},
        )

        # 7. Incorrect credentials
        res_wrong = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Bearer wrong-secret-token"},
        )

        all_responses = [
            res_missing,
            res_basic,
            res_empty,
            res_spaces,
            res_token,
            res_no_scheme,
            res_wrong,
        ]

        expected_body = {
            "type": "https://docs.virtujudge.org/problems/unauthorized",
            "title": "Unauthorized",
            "status": 401,
            "detail": "Invalid authentication credentials",
            "instance": f"/api/v1/internal/worker/ai-jobs/{job_id}",
            "code": "unauthorized",
        }

        for r in all_responses:
            assert r.status_code == 401
            assert r.headers.get("www-authenticate") == "Bearer"
            assert "application/problem+json" in r.headers.get("content-type", "")
            data = r.json()
            assert data["type"] == expected_body["type"]
            assert data["title"] == expected_body["title"]
            assert data["status"] == expected_body["status"]
            assert data["detail"] == expected_body["detail"]
            assert data["instance"] == expected_body["instance"]
            assert data["code"] == expected_body["code"]


@pytest.mark.anyio
async def test_worker_auth_job_existence_opacity() -> None:
    app, existing_jobs = _create_test_app("my-worker-secret")
    existing_job_id = next(iter(existing_jobs))
    non_existent_job_id = uuid4()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_existing = await client.get(f"/api/v1/internal/worker/ai-jobs/{existing_job_id}")
        res_non_existent = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{non_existent_job_id}"
        )

        assert res_existing.status_code == 401
        assert res_non_existent.status_code == 401
        assert res_existing.json()["detail"] == res_non_existent.json()["detail"]
        assert res_existing.json()["code"] == res_non_existent.json()["code"]

        # Request with wrong credentials for existing job vs non-existent job
        res_wrong_exist = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{existing_job_id}",
            headers={"Authorization": "Bearer bad-token"},
        )
        res_wrong_non_exist = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{non_existent_job_id}",
            headers={"Authorization": "Bearer bad-token"},
        )

        assert res_wrong_exist.status_code == 401
        assert res_wrong_non_exist.status_code == 401
        assert res_wrong_exist.json()["detail"] == res_wrong_non_exist.json()["detail"]


@pytest.mark.anyio
async def test_worker_auth_no_secret_or_credential_leakage_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configured_secret = "sensitive-shared-worker-token-xyz"
    app, existing_jobs = _create_test_app(configured_secret)
    job_id = next(iter(existing_jobs))

    transport = ASGITransport(app=app)
    attempted_bad_token = "attacker-supplied-attempt-secret-999"

    with caplog.at_level(logging.DEBUG):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                f"/api/v1/internal/worker/ai-jobs/{job_id}",
                headers={"Authorization": f"Bearer {attempted_bad_token}"},
            )
            assert res.status_code == 401

    # Neither the configured secret nor the attempted bad token may appear in any log record
    assert attempted_bad_token not in caplog.text
    assert configured_secret not in caplog.text


def test_production_startup_requires_worker_shared_secret() -> None:
    # Production without secret must raise WorkerAuthConfigurationError
    prod_no_secret = Settings(
        app_env="production",
        database_url="sqlite+aiosqlite:///:memory:",
        ai_worker_shared_secret=None,
    )
    with pytest.raises(WorkerAuthConfigurationError) as exc_info:
        create_app(prod_no_secret)
    assert "AI_WORKER_SHARED_SECRET must be configured in production" in str(exc_info.value)

    # Production with valid secret succeeds
    prod_with_secret = Settings(
        app_env="production",
        database_url="sqlite+aiosqlite:///:memory:",
        ai_worker_shared_secret=SecretStr("super-secure-production-secret"),
    )
    prod_app = create_app(prod_with_secret)
    assert prod_app is not None
    assert prod_app.state.worker_auth_verifier is not None


@pytest.mark.anyio
async def test_worker_auth_unconfigured_in_test_returns_503() -> None:
    # When worker auth is not configured at all in test/dev
    app, existing_jobs = _create_test_app(secret=None)
    job_id = next(iter(existing_jobs))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/internal/worker/ai-jobs/{job_id}",
            headers={"Authorization": "Bearer any-token"},
        )
        assert res.status_code == 503
        assert "application/problem+json" in res.headers.get("content-type", "")
        data = res.json()
        assert data["status"] == 503
        assert "Worker authentication provider is not configured" in data["detail"]
