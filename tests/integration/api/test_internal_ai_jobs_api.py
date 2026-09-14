from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.dependencies.services import get_ai_jobs
from app.application.ai_jobs import AIJobs
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.main import create_app
from app.settings import Settings
from tests.support.fake_analysis_job_repository import FakeAnalysisJobRepository

SHARED_SECRET = "test-worker-shared-secret-key-12345"


def _create_test_job(
    job_id: UUID | None = None,
    status: AnalysisJobStatus = AnalysisJobStatus.PENDING,
    last_update_sequence: int = 0,
    cancel_requested: bool = False,
    include_sensitive_fields: bool = False,
) -> AnalysisJob:
    now = datetime.now(UTC)
    payload = (
        {
            "presentation": {
                "artifact_id": str(uuid4()),
                "object_key": "private/raw/secret_video.mp4",
            },
            "internal_secret": "must-not-be-leaked",
        }
        if include_sensitive_fields
        else None
    )
    completed_result = (
        {"private_score": 95, "internal_evaluation": "do-not-expose"}
        if include_sensitive_fields
        else None
    )
    last_error = (
        "Internal DB failure at 192.168.1.1: secret stacktrace"
        if include_sensitive_fields
        else None
    )

    return AnalysisJob(
        id=job_id or uuid4(),
        practice_session_id=uuid4(),
        attempt_id=uuid4(),
        analysis_attempt=1,
        job_type="analyze_session",
        status=status,
        correlation_id=uuid4(),
        last_update_sequence=last_update_sequence,
        payload_version=1,
        attempts=1,
        cancel_requested=cancel_requested,
        retry_count=0,
        last_error=last_error,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        payload=payload,
        queued_at=now,
        next_dispatch_at=None,
        dispatch_retry_count=2 if include_sensitive_fields else 0,
        last_dispatch_error_category="temporary_error" if include_sensitive_fields else None,
        completed_result=completed_result,
    )


def _build_test_app() -> tuple[FastAPI, FakeAnalysisJobRepository, MagicMock]:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        ai_worker_shared_secret=SecretStr(SHARED_SECRET),
    )
    app = create_app(settings)
    fake_repo = FakeAnalysisJobRepository()
    fake_uow = MagicMock()
    fake_uow.jobs = fake_repo

    ai_jobs = AIJobs(fake_uow)
    ai_jobs_spy = MagicMock(wraps=ai_jobs)
    ai_jobs_spy.get_job = AsyncMock(side_effect=ai_jobs.get_job)

    app.dependency_overrides[get_ai_jobs] = lambda: ai_jobs_spy
    return app, fake_repo, ai_jobs_spy


@pytest.mark.anyio
async def test_get_internal_ai_job_status_success() -> None:
    app, repo, _ = _build_test_app()
    job = _create_test_job(
        status=AnalysisJobStatus.RUNNING,
        last_update_sequence=3,
        cancel_requested=True,
    )
    await repo.create(job)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/internal/v1/ai-jobs/{job.id}",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["id"] == str(job.id)
    assert data["job_id"] == str(job.id)
    assert data["status"] == "running"
    assert data["last_update_sequence"] == 3
    assert data["cancel_requested"] is True


@pytest.mark.anyio
async def test_get_internal_ai_job_status_unknown_job() -> None:
    app, _, _ = _build_test_app()
    unknown_job_id = uuid4()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/internal/v1/ai-jobs/{unknown_job_id}",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["status"] == 404
    assert data["code"] == "not_found"
    assert data["title"] == "Resource not found"
    assert data["detail"] == "AI job not found"
    assert data["instance"] == f"/internal/v1/ai-jobs/{unknown_job_id}"
    assert data["type"] == "https://docs.virtujudge.org/problems/not-found"


@pytest.mark.anyio
async def test_get_internal_ai_job_status_authentication_ordering() -> None:
    app, repo, ai_jobs_spy = _build_test_app()
    existing_job = _create_test_job()
    await repo.create(existing_job)
    unknown_job_id = uuid4()

    unauthenticated_headers: list[dict[str, str]] = [
        {},
        {"Authorization": "Bearer wrong-secret"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer    "},
        {"Authorization": "Token some-token"},
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for headers in unauthenticated_headers:
            res_existing = await client.get(
                f"/internal/v1/ai-jobs/{existing_job.id}",
                headers=headers,
            )
            assert res_existing.status_code == status.HTTP_401_UNAUTHORIZED
            assert res_existing.headers.get("www-authenticate") == "Bearer"
            assert res_existing.json()["code"] == "unauthorized"

            res_unknown = await client.get(
                f"/internal/v1/ai-jobs/{unknown_job_id}",
                headers=headers,
            )
            assert res_unknown.status_code == status.HTTP_401_UNAUTHORIZED
            assert res_unknown.headers.get("www-authenticate") == "Bearer"
            assert res_unknown.json()["code"] == "unauthorized"

    # ai_jobs.get_job must never have been called for unauthenticated requests
    assert ai_jobs_spy.get_job.call_count == 0


@pytest.mark.anyio
async def test_get_internal_ai_job_status_response_field_minimization() -> None:
    app, repo, _ = _build_test_app()
    sensitive_job = _create_test_job(include_sensitive_fields=True)
    await repo.create(sensitive_job)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/internal/v1/ai-jobs/{sensitive_job.id}",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Allowed minimum safe fields
    assert set(data.keys()) == {
        "id",
        "job_id",
        "status",
        "last_update_sequence",
        "cancel_requested",
    }

    # Prohibited fields must never leak
    forbidden_keys = [
        "team_id",
        "user_id",
        "practice_session_id",
        "attempt_id",
        "payload",
        "completed_result",
        "last_error",
        "last_dispatch_error_category",
        "dispatch_retry_count",
        "correlation_id",
        "object_key",
        "internal_secret",
        "private_score",
        "internal_evaluation",
    ]
    for key in forbidden_keys:
        assert key not in data


@pytest.mark.anyio
async def test_get_internal_ai_job_status_invalid_uuid() -> None:
    app, _, _ = _build_test_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/internal/v1/ai-jobs/not-a-valid-uuid",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
