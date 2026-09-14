import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.dependencies.services import get_ai_jobs
from app.application.ai_job_contracts import AIWorkerUpdate
from app.application.ai_jobs import AIJobs
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.main import create_app
from app.settings import Settings
from tests.support import FakeAnalysisJobRepository, FakeUnitOfWork

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
    fake_uow = FakeUnitOfWork(fake_repo)

    ai_jobs = AIJobs(fake_uow)
    ai_jobs_spy = MagicMock(wraps=ai_jobs)
    ai_jobs_spy.get_job = AsyncMock(side_effect=ai_jobs.get_job)
    ai_jobs_spy.record_update = AsyncMock(side_effect=ai_jobs.record_update)

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


def _valid_started_update(sequence: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "status": "started",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trc_test_12345",
        "payload": {
            "pipeline_version": "0.1.0",
        },
    }


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_authentication_ordering() -> None:
    app, repo, ai_jobs_spy = _build_test_app()
    existing_job = _create_test_job()
    await repo.create(existing_job)
    unknown_job_id = uuid4()
    body = _valid_started_update()

    unauthenticated_headers: list[dict[str, str]] = [
        {},
        {"Authorization": "Bearer wrong-secret"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer    "},
        {"Authorization": "Token some-token"},
        {"Authorization": "some-secret"},
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for headers in unauthenticated_headers:
            # Existing job
            res_existing = await client.post(
                f"/internal/v1/ai-jobs/{existing_job.id}/updates",
                headers=headers,
                json=body,
            )
            assert res_existing.status_code == status.HTTP_401_UNAUTHORIZED
            assert res_existing.headers.get("www-authenticate") == "Bearer"
            assert "application/problem+json" in res_existing.headers.get("content-type", "")
            data_exist = res_existing.json()
            assert data_exist["status"] == 401
            assert data_exist["code"] == "unauthorized"
            assert data_exist["title"] == "Unauthorized"
            assert data_exist["detail"] == "Invalid authentication credentials"
            assert data_exist["type"] == "https://docs.virtujudge.org/problems/unauthorized"
            assert data_exist["instance"] == f"/internal/v1/ai-jobs/{existing_job.id}/updates"

            # Unknown job (existence opacity: indistinguishable 401)
            res_unknown = await client.post(
                f"/internal/v1/ai-jobs/{unknown_job_id}/updates",
                headers=headers,
                json=body,
            )
            assert res_unknown.status_code == status.HTTP_401_UNAUTHORIZED
            assert res_unknown.headers.get("www-authenticate") == "Bearer"
            assert "application/problem+json" in res_unknown.headers.get("content-type", "")
            data_unknown = res_unknown.json()
            assert data_unknown["code"] == "unauthorized"
            assert data_unknown["detail"] == "Invalid authentication credentials"

    # ai_jobs.record_update must never have been called for unauthenticated requests
    assert ai_jobs_spy.record_update.call_count == 0


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_authenticated_unknown_job() -> None:
    app, _, ai_jobs_spy = _build_test_app()
    unknown_job_id = uuid4()
    body = _valid_started_update()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/internal/v1/ai-jobs/{unknown_job_id}/updates",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            json=body,
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["status"] == 404
    assert data["code"] == "not_found"
    assert data["title"] == "Resource not found"
    assert data["detail"] == "AI job not found"
    assert data["instance"] == f"/internal/v1/ai-jobs/{unknown_job_id}/updates"
    assert data["type"] == "https://docs.virtujudge.org/problems/not-found"

    # Verify delegation was attempted with the unknown job ID
    assert ai_jobs_spy.record_update.call_count == 1
    call_args = ai_jobs_spy.record_update.call_args
    assert call_args[0][0] == unknown_job_id


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_validation_malformed_input() -> None:
    app, repo, ai_jobs_spy = _build_test_app()
    job = _create_test_job()
    await repo.create(job)

    malformed_payloads: list[tuple[str, Any]] = [
        (
            "missing_sequence",
            {
                "schema_version": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "sequence_zero",
            {
                "schema_version": 1,
                "sequence": 0,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "sequence_negative",
            {
                "schema_version": 1,
                "sequence": -5,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "invalid_schema_version",
            {
                "schema_version": 2,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "invalid_status",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "in_progress",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"stage": "speech"},
            },
        ),
        (
            "missing_occurred_at",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "invalid_occurred_at",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": "not-a-datetime",
                "trace_id": "trc_1",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "missing_trace_id",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "empty_trace_id",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "",
                "payload": {"pipeline_version": "0.1.0"},
            },
        ),
        (
            "missing_payload",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
            },
        ),
        (
            "started_missing_pipeline_version",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "started",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {},
            },
        ),
        (
            "progress_excessive_value",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "progress",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"stage": "speech", "progress": 1.5, "message": "msg"},
            },
        ),
        (
            "progress_negative_value",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "progress",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"stage": "speech", "progress": -0.1, "message": "msg"},
            },
        ),
        (
            "progress_missing_stage",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "progress",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"progress": 0.5, "message": "msg"},
            },
        ),
        (
            "failed_missing_code",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "failed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {
                    "stage": "speech",
                    "retryable": False,
                    "attempts": 1,
                    "message": "msg",
                },
            },
        ),
        (
            "failed_negative_attempts",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "failed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {
                    "stage": "speech",
                    "code": "err",
                    "retryable": False,
                    "attempts": -1,
                    "message": "msg",
                },
            },
        ),
        (
            "cancelled_with_extra_fields",
            {
                "schema_version": 1,
                "sequence": 1,
                "status": "cancelled",
                "occurred_at": datetime.now(UTC).isoformat(),
                "trace_id": "trc_1",
                "payload": {"unexpected": "field"},
            },
        ),
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for desc, body in malformed_payloads:
            res = await client.post(
                f"/internal/v1/ai-jobs/{job.id}/updates",
                headers={"Authorization": f"Bearer {SHARED_SECRET}"},
                json=body,
            )
            assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, (
                f"Failed for {desc}: {res.text}"
            )
            assert res.headers["content-type"] == "application/problem+json", (
                f"Wrong content-type for {desc}"
            )
            data = res.json()
            assert data["status"] == 422
            assert data["code"] == "validation_failed"
            assert data["title"] == "Validation failed"
            assert data["detail"] == "The request parameters failed validation."
            assert data["type"] == "https://docs.virtujudge.org/problems/validation-failed"
            assert data["instance"] == f"/internal/v1/ai-jobs/{job.id}/updates"

        # Malformed JSON syntax
        res_syntax = await client.post(
            f"/internal/v1/ai-jobs/{job.id}/updates",
            headers={
                "Authorization": f"Bearer {SHARED_SECRET}",
                "Content-Type": "application/json",
            },
            content=b'{"schema_version": ',
        )
        assert res_syntax.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert res_syntax.headers["content-type"] == "application/problem+json"
        assert res_syntax.json()["code"] == "validation_failed"

        # Invalid UUID path parameter
        res_uuid = await client.post(
            "/internal/v1/ai-jobs/not-a-valid-uuid/updates",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            json=_valid_started_update(),
        )
        assert res_uuid.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert res_uuid.headers["content-type"] == "application/problem+json"
        assert res_uuid.json()["code"] == "validation_failed"

    # ai_jobs.record_update must never have been called for malformed inputs
    assert ai_jobs_spy.record_update.call_count == 0


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_delegation() -> None:
    app, repo, ai_jobs_spy = _build_test_app()
    job = _create_test_job(status=AnalysisJobStatus.PENDING, last_update_sequence=0)
    await repo.create(job)

    valid_updates: list[dict[str, Any]] = [
        {
            "schema_version": 1,
            "sequence": 1,
            "status": "started",
            "occurred_at": "2026-09-14T10:00:00Z",
            "trace_id": "trc_started_1",
            "payload": {"pipeline_version": "0.1.0"},
        },
        {
            "schema_version": 1,
            "sequence": 2,
            "status": "progress",
            "occurred_at": "2026-09-14T10:01:00Z",
            "trace_id": "trc_progress_2",
            "payload": {
                "stage": "speech",
                "progress": 0.45,
                "message": "Transcribing presentation",
            },
        },
        {
            "schema_version": 1,
            "sequence": 3,
            "status": "completed",
            "occurred_at": "2026-09-14T10:02:00Z",
            "trace_id": "trc_completed_3",
            "payload": {
                "analysis_artifact": {
                    "artifact_id": str(uuid4()),
                    "object_key": "ai/session/analysis.json",
                    "checksum": (
                        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                    ),
                    "schema_version": 1,
                },
                "primary_questions": [
                    {
                        "candidate_id": "q-1",
                        "text": "What evidence supports conversion rate?",
                        "reason": "Pitch lacks source.",
                        "rubric_dimension": "pitch_content_and_evidence",
                        "evidence_ids": ["ev_1"],
                    },
                    {
                        "candidate_id": "q-2",
                        "text": "Which technical assumption is highest risk?",
                        "reason": "Unranked dependencies.",
                        "rubric_dimension": "technical_feasibility",
                        "evidence_ids": ["ev_2"],
                    },
                    {
                        "candidate_id": "q-3",
                        "text": "How was customer segment validated?",
                        "reason": "Unvalidated segment.",
                        "rubric_dimension": "business_reasoning",
                        "evidence_ids": ["ev_3"],
                    },
                ],
                "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                "limitations": [],
            },
        },
        {
            "schema_version": 1,
            "sequence": 4,
            "status": "failed",
            "occurred_at": "2026-09-14T10:03:00Z",
            "trace_id": "trc_failed_4",
            "payload": {
                "stage": "speech",
                "code": "provider_timeout",
                "retryable": True,
                "attempts": 3,
                "message": "Speech analysis timed out",
            },
        },
        {
            "schema_version": 1,
            "sequence": 5,
            "status": "cancelled",
            "occurred_at": "2026-09-14T10:04:00Z",
            "trace_id": "trc_cancelled_5",
            "payload": {},
        },
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for idx, body in enumerate(valid_updates, start=1):
            update_status = body["status"]
            job_status = (
                AnalysisJobStatus.PENDING
                if update_status == "started"
                else AnalysisJobStatus.RUNNING
            )
            target_job = _create_test_job(status=job_status, last_update_sequence=0)
            await repo.create(target_job)

            res = await client.post(
                f"/internal/v1/ai-jobs/{target_job.id}/updates",
                headers={"Authorization": f"Bearer {SHARED_SECRET}"},
                json=body,
            )
            assert res.status_code == status.HTTP_200_OK, (
                f"Failed on update {body['status']}: {res.text}"
            )
            assert ai_jobs_spy.record_update.call_count == idx

            call_args = ai_jobs_spy.record_update.call_args
            called_job_id, update_arg = call_args[0]
            assert called_job_id == target_job.id
            assert isinstance(update_arg, AIWorkerUpdate)
            assert update_arg.sequence == body["sequence"]
            assert update_arg.status == body["status"]
            assert update_arg.trace_id == body["trace_id"]


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_response_mapping_applied_and_ignored() -> None:
    app, _, ai_jobs_spy = _build_test_app()
    job_id = uuid4()
    body = _valid_started_update(sequence=1)

    # 1. Applied update scenario: record_update returns updated job
    applied_job = _create_test_job(
        job_id=job_id,
        status=AnalysisJobStatus.RUNNING,
        last_update_sequence=1,
        cancel_requested=False,
        include_sensitive_fields=True,
    )
    ai_jobs_spy.record_update.side_effect = None
    ai_jobs_spy.record_update.return_value = applied_job

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_applied = await client.post(
            f"/internal/v1/ai-jobs/{job_id}/updates",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            json=body,
        )

    assert res_applied.status_code == status.HTTP_200_OK
    assert res_applied.headers["content-type"].startswith("application/json")
    data_applied = res_applied.json()
    assert data_applied["id"] == str(job_id)
    assert data_applied["job_id"] == str(job_id)
    assert data_applied["status"] == "running"
    assert data_applied["last_update_sequence"] == 1
    assert data_applied["cancel_requested"] is False

    # Field minimization check: forbidden fields must NOT leak
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
        assert key not in data_applied

    assert set(data_applied.keys()) == {
        "id",
        "job_id",
        "status",
        "last_update_sequence",
        "cancel_requested",
    }

    # 2. Ignored update scenario (e.g. duplicate or out-of-order sequence):
    # record_update returns existing job state unchanged
    ignored_job = _create_test_job(
        job_id=job_id,
        status=AnalysisJobStatus.RUNNING,
        last_update_sequence=3,
        cancel_requested=True,
    )
    ai_jobs_spy.record_update.return_value = ignored_job

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_ignored = await client.post(
            f"/internal/v1/ai-jobs/{job_id}/updates",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            json=_valid_started_update(sequence=2),  # Stale sequence
        )

    assert res_ignored.status_code == status.HTTP_200_OK
    data_ignored = res_ignored.json()
    assert data_ignored["id"] == str(job_id)
    assert data_ignored["status"] == "running"
    assert data_ignored["last_update_sequence"] == 3
    assert data_ignored["cancel_requested"] is True


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_no_callback_body_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, repo, _ = _build_test_app()
    job = _create_test_job(status=AnalysisJobStatus.RUNNING, last_update_sequence=0)
    await repo.create(job)

    secret_marker_payload = "SECRET_CALLBACK_PAYLOAD_CONTENT_77777"
    secret_marker_stage = "SECRET_STAGE_LABEL_99999"

    update_body = {
        "schema_version": 1,
        "sequence": 1,
        "status": "progress",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trc_progress_secret",
        "payload": {
            "stage": secret_marker_stage,
            "progress": 0.5,
            "message": secret_marker_payload,
        },
    }

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.DEBUG):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                f"/internal/v1/ai-jobs/{job.id}/updates",
                headers={"Authorization": f"Bearer {SHARED_SECRET}"},
                json=update_body,
            )

    assert res.status_code == status.HTTP_200_OK

    # Verify no callback body contents or secret markers leaked into logs
    assert secret_marker_payload not in caplog.text
    assert secret_marker_stage not in caplog.text
    assert SHARED_SECRET not in caplog.text


@pytest.mark.anyio
async def test_post_internal_ai_job_updates_invalid_transition_returns_409() -> None:
    app, repo, _ = _build_test_app()
    job = _create_test_job(status=AnalysisJobStatus.COMPLETED, last_update_sequence=3)
    await repo.create(job)

    revive_update = _valid_started_update(sequence=4)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            f"/internal/v1/ai-jobs/{job.id}/updates",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            json=revive_update,
        )

    assert res.status_code == status.HTTP_409_CONFLICT
    assert res.headers["content-type"] == "application/problem+json"
    data = res.json()
    assert data["status"] == 409
    assert data["code"] == "http_409"
