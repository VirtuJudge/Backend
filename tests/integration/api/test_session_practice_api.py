from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.session_workflow import get_session_workflow
from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    AnalysisNotReady,
    ConsentRequiredError,
    IdempotencyConflict,
    InvalidSessionStatusTransition,
    InvalidSpeakerLabel,
    InvalidTeamMember,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnverifiedAsset,
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


def _create_session(
    status: SessionStatus = SessionStatus.DRAFT, version: int = 1
) -> PracticeSession:
    now = datetime.now(UTC)
    return PracticeSession(
        id=uuid4(),
        project_id=uuid4(),
        created_by=uuid4(),
        name="Test Practice Session",
        status=status,
        version=version,
        created_at=now,
        updated_at=now,
        consent_granted=False,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
    )


def _create_attempt(
    session_id: UUID,
    status: AnalysisAttemptStatus = AnalysisAttemptStatus.QUEUED,
    attempt_number: int = 1,
) -> AnalysisAttempt:
    now = datetime.now(UTC)
    return AnalysisAttempt(
        id=uuid4(),
        session_id=session_id,
        manifest_id=uuid4(),
        idempotency_key="key-123",
        attempt_number=attempt_number,
        status=status,
        failure_code=None,
        failure_message=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )


@pytest.fixture
def workflow_mock() -> MagicMock:
    mock = MagicMock(spec=SessionWorkflow)
    mock.create_session = AsyncMock()
    mock.get_session = AsyncMock(return_value=_create_session(status=SessionStatus.READY))
    mock.get_manifest = AsyncMock(return_value=None)
    mock.list_sessions = AsyncMock()
    mock.update_session = AsyncMock()
    mock.create_analysis_attempt = AsyncMock()
    mock.get_analysis_attempts = AsyncMock()
    mock.retry = AsyncMock()
    mock.cancel = AsyncMock()
    mock.update_speaker_mappings = AsyncMock()
    return mock


@pytest.fixture
def current_user() -> User:
    return _create_user()


@pytest.fixture
def client(workflow_mock: MagicMock, current_user: User) -> AsyncClient:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_session_workflow] = lambda: workflow_mock

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.anyio
async def test_create_practice_session_returns_201_with_etag_and_location(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session = _create_session(status=SessionStatus.DRAFT, version=1)
    workflow_mock.create_session.return_value = session

    async with client:
        response = await client.post(
            f"/api/v1/projects/{session.project_id}/practice-sessions",
            json={
                "name": session.name,
                "presentation_asset_version_id": str(uuid4()),
                "supporting_document_version_ids": [str(uuid4())],
                "rubric": {"rubric_id": "startup_pitch", "version": 1},
            },
        )

    assert response.status_code == 201
    assert response.headers["ETag"] == '"1"'
    assert response.headers["Location"] == f"/api/v1/practice-sessions/{session.id}"
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["status"] == "draft"


@pytest.mark.anyio
async def test_get_practice_session_returns_200_with_etag(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session = _create_session(status=SessionStatus.READY, version=2)
    workflow_mock.get_session.return_value = session

    async with client:
        response = await client.get(f"/api/v1/practice-sessions/{session.id}")

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["version"] == 2


@pytest.mark.anyio
async def test_get_practice_session_returns_404_when_not_found(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    workflow_mock.get_session.side_effect = SessionNotFoundError("Session not found")
    missing_id = uuid4()

    async with client:
        response = await client.get(f"/api/v1/practice-sessions/{missing_id}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "not_found"
    assert data["status"] == 404


@pytest.mark.anyio
async def test_update_practice_session_returns_412_on_stale_etag(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    workflow_mock.update_session.side_effect = StaleEntityVersion("Stale version")
    session_id = uuid4()

    async with client:
        response = await client.patch(
            f"/api/v1/practice-sessions/{session_id}",
            json={"name": "New Name"},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 412
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "precondition_failed"


@pytest.mark.anyio
async def test_update_practice_session_returns_409_when_manifest_frozen(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    workflow_mock.update_session.side_effect = ManifestAlreadyFrozen("Manifest is frozen")
    session_id = uuid4()

    async with client:
        response = await client.patch(
            f"/api/v1/practice-sessions/{session_id}",
            json={"name": "New Name"},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "manifest_frozen"


@pytest.mark.anyio
async def test_create_analysis_attempt_returns_202_with_location(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    attempt = _create_attempt(session_id=session_id)
    workflow_mock.get_session.return_value = _create_session(status=SessionStatus.READY)
    workflow_mock.create_analysis_attempt.return_value = attempt

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "test-idempotency-key-1"},
        )

    assert response.status_code == 202
    assert (
        response.headers["Location"]
        == f"/api/v1/practice-sessions/{session_id}/analysis-attempts/{attempt.id}"
    )
    data = response.json()
    assert data["id"] == str(attempt.id)
    assert data["status"] == "queued"


@pytest.mark.anyio
async def test_create_analysis_attempt_returns_409_when_session_not_ready(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.get_session.return_value = _create_session(status=SessionStatus.READY)
    workflow_mock.create_analysis_attempt.side_effect = SessionNotReadyError("Session is not ready")

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "test-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "session_not_ready"


@pytest.mark.anyio
async def test_create_analysis_attempt_returns_422_when_consent_required(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.get_session.return_value = _create_session(status=SessionStatus.READY)
    workflow_mock.create_analysis_attempt.side_effect = ConsentRequiredError(
        "Consent must be accepted"
    )

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": False, "policy_version": 1}},
            headers={"Idempotency-Key": "test-idempotency-key-1"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "consent_required"


@pytest.mark.anyio
async def test_retry_analysis_attempt_returns_202_with_location(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    attempt = _create_attempt(session_id=session_id, attempt_number=2)
    workflow_mock.retry.return_value = attempt

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/retries",
            headers={"Idempotency-Key": "retry-idempotency-key-1"},
        )

    assert response.status_code == 202
    assert (
        response.headers["Location"]
        == f"/api/v1/practice-sessions/{session_id}/analysis-attempts/{attempt.id}"
    )
    data = response.json()
    assert data["attempt_number"] == 2


@pytest.mark.anyio
async def test_retry_analysis_attempt_returns_409_when_retry_not_allowed(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.retry.side_effect = RetryNotAllowed("Only failed attempts can be retried")

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/retries",
            headers={"Idempotency-Key": "retry-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "retry_not_allowed"


@pytest.mark.anyio
async def test_cancel_practice_session_returns_202_with_location_and_etag(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session = _create_session(status=SessionStatus.CANCELLED, version=3)
    workflow_mock.cancel.return_value = session

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session.id}/cancel",
            json={"reason": "User requested cancellation"},
            headers={"Idempotency-Key": "cancel-idempotency-key-1"},
        )

    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/practice-sessions/{session.id}"
    assert response.headers["ETag"] == '"3"'
    data = response.json()
    assert data["status"] == "cancelled"


@pytest.mark.anyio
async def test_cancel_practice_session_returns_409_terminal_state(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.cancel.side_effect = InvalidSessionStatusTransition(
        "A completed session cannot be cancelled"
    )

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/cancel",
            json={"reason": "Too late"},
            headers={"Idempotency-Key": "cancel-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "terminal_state"


@pytest.mark.anyio
async def test_cancel_practice_session_returns_409_on_idempotency_conflict(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.cancel.side_effect = IdempotencyConflict(
        "Idempotency key reused with different payload."
    )

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/cancel",
            json={"reason": "Different reason"},
            headers={"Idempotency-Key": "cancel-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_update_speaker_mappings_returns_200_with_etag(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    session = _create_session(status=SessionStatus.ANALYZING, version=2)
    workflow_mock.get_session.return_value = session

    user_id = uuid4()
    mapping = SpeakerMapping(
        id=uuid4(),
        attempt_id=uuid4(),
        speaker_label="SPEAKER_00",
        member_id=user_id,
        mapped_by=uuid4(),
        mapped_at=datetime.now(UTC),
        user_id=user_id,
    )
    workflow_mock.update_speaker_mappings.return_value = [mapping]

    async with client:
        response = await client.put(
            f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
            json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(user_id)}]},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    data = response.json()
    assert len(data) == 1
    assert data[0]["speaker_label"] == "SPEAKER_00"


@pytest.mark.anyio
async def test_update_speaker_mappings_returns_412_on_stale_etag(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.update_speaker_mappings.side_effect = StaleEntityVersion("Stale ETag")

    async with client:
        response = await client.put(
            f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
            json={"mappings": []},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 412
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "precondition_failed"


@pytest.mark.anyio
async def test_update_speaker_mappings_returns_409_when_analysis_not_ready(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.update_speaker_mappings.side_effect = AnalysisNotReady("Results not ready")

    async with client:
        response = await client.put(
            f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
            json={"mappings": []},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "analysis_not_ready"


@pytest.mark.anyio
async def test_update_speaker_mappings_returns_422_when_invalid_label(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.update_speaker_mappings.side_effect = InvalidSpeakerLabel("Unknown label")

    async with client:
        response = await client.put(
            f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
            json={"mappings": [{"speaker_label": "UNKNOWN", "user_id": str(uuid4())}]},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "invalid_label"


@pytest.mark.anyio
async def test_update_speaker_mappings_returns_422_when_invalid_member(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.update_speaker_mappings.side_effect = InvalidTeamMember("Not a team member")

    async with client:
        response = await client.put(
            f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
            json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(uuid4())}]},
            headers={"If-Match": '"1"'},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "invalid_member"


@pytest.mark.anyio
async def test_create_analysis_attempt_returns_409_on_idempotency_conflict(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.create_analysis_attempt.side_effect = IdempotencyConflict()

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "same-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_retry_returns_409_on_idempotency_conflict(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.retry.side_effect = IdempotencyConflict()

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/retries",
            headers={"Idempotency-Key": "same-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_cancel_returns_409_on_idempotency_conflict(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.cancel.side_effect = IdempotencyConflict()

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/cancel",
            json={"reason": "Test cancel"},
            headers={"Idempotency-Key": "same-idempotency-key-1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_unauthenticated_request_returns_401_problem_details_with_trace_id(
    workflow_mock: MagicMock,
) -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    app.dependency_overrides[get_session_workflow] = lambda: workflow_mock

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as unauth_client:
        response = await unauth_client.get(f"/api/v1/practice-sessions/{uuid4()}")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    trace_id = response.headers.get("X-Correlation-Id")
    assert trace_id is not None
    data = response.json()
    assert data["code"] == "unauthorized"
    assert data["status"] == 401
    assert data["trace_id"] == trace_id


@pytest.mark.anyio
async def test_correlation_id_echoes_client_header_or_generates(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session = _create_session(status=SessionStatus.READY, version=1)
    workflow_mock.get_session.return_value = session

    custom_trace = "custom-trace-id-12345"
    async with client:
        response = await client.get(
            f"/api/v1/practice-sessions/{session.id}",
            headers={"X-Correlation-Id": custom_trace},
        )
        assert response.headers["X-Correlation-Id"] == custom_trace

        response_generated = await client.get(f"/api/v1/practice-sessions/{session.id}")
    gen_id = response_generated.headers.get("X-Correlation-Id")
    assert gen_id is not None
    assert len(gen_id) > 0


@pytest.mark.anyio
async def test_idempotency_key_length_validation(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.create_analysis_attempt.return_value = _create_attempt(session_id)
    async with client:
        # Too short (< 16 chars)
        res_short = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "short-key"},
        )
        assert res_short.status_code == 422
        assert res_short.headers["content-type"] == "application/problem+json"
        data = res_short.json()
        assert data["code"] == "validation_failed"
        assert data.get("trace_id") is not None

        # Too long (> 128 chars)
        res_long = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "x" * 129},
        )
        assert res_long.status_code == 422
        assert res_long.headers["content-type"] == "application/problem+json"

        # Exactly 16 chars succeeds
        res_16 = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "a" * 16},
        )
        assert res_16.status_code == 202

        # Exactly 128 chars succeeds
        res_128 = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "b" * 128},
        )
        assert res_128.status_code == 202


@pytest.mark.anyio
async def test_create_session_rejects_duplicate_documents_in_api(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    project_id = uuid4()
    dup_id = str(uuid4())
    async with client:
        response = await client.post(
            f"/api/v1/projects/{project_id}/practice-sessions",
            json={
                "name": "Session with dupes",
                "presentation_asset_version_id": str(uuid4()),
                "supporting_document_version_ids": [dup_id, dup_id],
            },
        )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "validation_failed"
    assert data.get("trace_id") is not None


@pytest.mark.anyio
async def test_create_session_rejects_more_than_five_documents_in_api(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    project_id = uuid4()
    async with client:
        response = await client.post(
            f"/api/v1/projects/{project_id}/practice-sessions",
            json={
                "name": "Session with 6 docs",
                "presentation_asset_version_id": str(uuid4()),
                "supporting_document_version_ids": [str(uuid4()) for _ in range(6)],
            },
        )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "validation_failed"
    assert data.get("trace_id") is not None


@pytest.mark.anyio
async def test_draft_session_start_returns_409_session_not_ready_without_mutation(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.create_analysis_attempt.side_effect = SessionNotReadyError("Session is not ready")

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "test-idempotency-key-ready-check"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "session_not_ready"
    assert data.get("trace_id") is not None


@pytest.mark.anyio
async def test_created_session_can_be_updated_to_ready_and_started_via_api(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    draft_session = _create_session(status=SessionStatus.DRAFT, version=1)
    ready_session = _create_session(status=SessionStatus.READY, version=2)
    ready_session.id = draft_session.id
    ready_session.project_id = draft_session.project_id
    workflow_mock.create_session.return_value = draft_session
    workflow_mock.update_session.return_value = ready_session

    attempt = _create_attempt(draft_session.id, status=AnalysisAttemptStatus.QUEUED)
    workflow_mock.create_analysis_attempt.return_value = attempt

    async with client:
        create_res = await client.post(
            f"/api/v1/projects/{draft_session.project_id}/practice-sessions",
            json={
                "name": draft_session.name,
                "presentation_asset_version_id": str(uuid4()),
                "supporting_document_version_ids": [str(uuid4())],
                "rubric": {"rubric_id": "startup_pitch", "version": 1},
            },
        )
        assert create_res.status_code == 201
        created_data = create_res.json()
        assert created_data["status"] == "draft"

        ready_res = await client.patch(
            f"/api/v1/practice-sessions/{draft_session.id}",
            json={"name": draft_session.name},
            headers={"If-Match": '"1"'},
        )
        assert ready_res.status_code == 200
        assert ready_res.json()["status"] == "ready"

        start_res = await client.post(
            f"/api/v1/practice-sessions/{draft_session.id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "start-key-valid-16"},
        )
        assert start_res.status_code == 202
        assert (
            start_res.headers["Location"]
            == f"/api/v1/practice-sessions/{draft_session.id}/analysis-attempts/{attempt.id}"
        )


@pytest.mark.anyio
async def test_unverified_session_cannot_be_created_or_started(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    workflow_mock.create_session.side_effect = UnverifiedAsset("Assets not verified")
    workflow_mock.create_analysis_attempt.side_effect = UnverifiedAsset("Assets not verified")

    async with client:
        create_res = await client.post(
            f"/api/v1/projects/{project_id}/practice-sessions",
            json={
                "name": "Unverified Session",
                "presentation_asset_version_id": str(uuid4()),
                "supporting_document_version_ids": [],
            },
        )
        assert create_res.status_code == 400
        assert create_res.headers["content-type"] == "application/problem+json"
        assert create_res.json()["code"] == "unverified_asset"

        start_res = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 1}},
            headers={"Idempotency-Key": "start-unverified-key"},
        )
        assert start_res.status_code == 409
        assert start_res.headers["content-type"] == "application/problem+json"
        assert start_res.json()["code"] == "session_not_ready"


@pytest.mark.anyio
async def test_cancel_session_concurrent_race_stale_version_returns_202(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session = _create_session(status=SessionStatus.CANCELLED, version=3)
    workflow_mock.cancel.return_value = session

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session.id}/cancel",
            json={"reason": "concurrent race"},
            headers={"Idempotency-Key": "cancel-race-valid-16"},
        )

    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/practice-sessions/{session.id}"
    assert response.headers["ETag"] == '"3"'
    data = response.json()
    assert data["status"] == "cancelled"


@pytest.mark.anyio
async def test_start_analysis_attempt_idempotency_conflict_with_different_consent(
    client: AsyncClient, workflow_mock: MagicMock
) -> None:
    session_id = uuid4()
    workflow_mock.create_analysis_attempt.side_effect = IdempotencyConflict(
        "Idempotency key has already been used with different parameters."
    )

    async with client:
        response = await client.post(
            f"/api/v1/practice-sessions/{session_id}/analysis-attempts",
            json={"consent": {"accepted": True, "policy_version": 2}},
            headers={"Idempotency-Key": "conflict-consent-key"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "idempotency_conflict"
    assert data.get("trace_id") is not None
