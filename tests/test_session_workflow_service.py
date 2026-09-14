import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AnalyzeSessionPayload,
)
from app.application.ai_jobs import AIJobs
from app.application.ports.ai_queue import AIJobQueueTemporaryFailure
from app.application.session_workflow import CURRENT_CONSENT_POLICY_VERSION, SessionWorkflow
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    AnalysisNotReady,
    ConsentPolicyOutdated,
    ConsentRequiredError,
    IdempotencyConflict,
    InvalidManifestDocuments,
    InvalidSessionStatusTransition,
    InvalidSpeakerLabel,
    InvalidTeamMember,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnauthorizedSessionAction,
    UnverifiedAsset,
)
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fake_session_notifications import FakeSessionNotifications


@pytest.fixture
def uow() -> MagicMock:
    uow = MagicMock()

    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    uow.projects = MagicMock()
    uow.sessions = MagicMock()
    uow.manifests = MagicMock()
    uow.attempts = MagicMock()
    uow.jobs = MagicMock()

    async def _mock_snapshots(vids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        return {vid: {"asset_id": uuid4(), "checksum": "dummy_checksum"} for vid in vids}

    uow.projects.get_by_id = AsyncMock()
    uow.projects.is_member = AsyncMock(return_value=True)
    uow.projects.is_owner = AsyncMock(return_value=True)
    uow.projects.asset_versions_are_verified = AsyncMock(return_value=True)
    uow.projects.get_asset_version_snapshots = AsyncMock(side_effect=_mock_snapshots)
    uow.projects.get_team_member_id = AsyncMock(return_value=uuid4())

    uow.sessions.get_by_id = AsyncMock()

    async def _get_for_update(sid: UUID) -> Any:
        return await uow.sessions.get_by_id(sid)

    uow.sessions.get_by_id_for_update = AsyncMock(side_effect=_get_for_update)
    uow.sessions.create = AsyncMock()
    uow.sessions.update = AsyncMock()
    uow.sessions.list_by_project = AsyncMock()

    uow.manifests.get_by_session_id = AsyncMock()
    uow.manifests.create = AsyncMock()
    uow.manifests.update = AsyncMock()

    uow.attempts.get_by_idempotency_key = AsyncMock(return_value=None)
    uow.attempts.get_next_attempt_number = AsyncMock(return_value=1)
    uow.attempts.get_all_by_session_id = AsyncMock()
    uow.attempts.get_latest = AsyncMock(return_value=None)
    uow.attempts.get_active = AsyncMock(return_value=None)
    uow.attempts.get_current_by_session_id = AsyncMock(return_value=None)
    uow.attempts.create = AsyncMock()
    uow.attempts.update = AsyncMock()

    uow.jobs.create = AsyncMock()
    uow.jobs.get_by_attempt_id = AsyncMock(return_value=None)
    uow.jobs.get_by_id = AsyncMock(return_value=None)
    uow.jobs.change_pending_to_queued = AsyncMock(return_value=None)
    uow.jobs.record_dispatch_failure = AsyncMock(return_value=None)
    uow.jobs.update = AsyncMock()

    uow.speaker_mappings = MagicMock()
    uow.speaker_mappings.replace_mappings = AsyncMock()
    uow.speaker_mappings.get_by_attempt_id = AsyncMock(return_value=[])
    uow.speaker_mappings.get_by_speaker_label = AsyncMock(return_value=None)
    uow.speaker_mappings.create = AsyncMock()
    uow.speaker_mappings.update = AsyncMock()

    uow.idempotency = MagicMock()
    uow.idempotency.get = AsyncMock(return_value=None)
    uow.idempotency.create = AsyncMock()

    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()

    return uow


@pytest.fixture
def notifications() -> FakeSessionNotifications:
    return FakeSessionNotifications()


@pytest.fixture
def workflow(uow: MagicMock, notifications: FakeSessionNotifications) -> SessionWorkflow:
    reader = MagicMock()
    reader.get_speaker_labels = AsyncMock(return_value={"SPEAKER_00", "SPEAKER_01"})
    return SessionWorkflow(
        uow,
        diarization_reader=reader,
        notifications=notifications,
        trace_id="trace-test",
    )


@pytest.fixture
def project_id() -> UUID:
    return uuid4()


@pytest.fixture
def actor_id() -> UUID:
    return uuid4()


@pytest.fixture
def practice_session(
    project_id: UUID,
    actor_id: UUID,
) -> PracticeSession:
    now = datetime.now(UTC)

    return PracticeSession(
        id=uuid4(),
        name="Test Session",
        project_id=project_id,
        created_by=actor_id,
        status=SessionStatus.DRAFT,
        version=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        consent_granted=False,
    )


@pytest.fixture
def manifest() -> SessionManifest:
    return SessionManifest(
        id=uuid4(),
        session_id=uuid4(),
        presentation_version_id=uuid4(),
        supporting_document_version_ids=[uuid4()],
        frozen_at=None,
    )


@pytest.mark.anyio
async def test_create_session_creates_session_and_manifest(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    project = MagicMock()

    uow.projects.get_by_id.return_value = project
    uow.projects.is_member.return_value = True

    presentation_version_id = uuid4()
    document_version_id = uuid4()

    result = await workflow.create_session(
        project_id=project_id,
        actor_id=actor_id,
        name="My Session",
        presentation_asset_version_id=presentation_version_id,
        supporting_document_version_ids=[document_version_id],
    )

    assert result.project_id == project_id
    assert result.created_by == actor_id
    assert result.name == "My Session"
    assert result.status == SessionStatus.DRAFT

    uow.projects.get_by_id.assert_awaited_once_with(project_id)
    uow.projects.is_member.assert_awaited_once_with(
        project_id=project_id,
        user_id=actor_id,
    )

    uow.sessions.create.assert_awaited_once()
    uow.manifests.create.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_create_session_publishes_after_commit(
    workflow: SessionWorkflow,
    uow: MagicMock,
    notifications: FakeSessionNotifications,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.get_by_id.return_value = MagicMock()

    session = await workflow.create_session(
        project_id=project_id,
        actor_id=actor_id,
        name="My Session",
        presentation_asset_version_id=uuid4(),
    )

    events = (await notifications.replay(str(session.id), 0)).events
    assert len(events) == 1
    assert events[0].state == SessionStatus.DRAFT  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_notification_failure_does_not_fail_committed_session(
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    notifications = MagicMock()
    notifications.publish = AsyncMock(side_effect=RuntimeError("unavailable"))
    workflow = SessionWorkflow(uow, notifications=notifications)
    uow.projects.get_by_id.return_value = MagicMock()

    result = await workflow.create_session(
        project_id=project_id,
        actor_id=actor_id,
        name="My Session",
        presentation_asset_version_id=uuid4(),
    )

    assert result.status == SessionStatus.DRAFT
    uow.commit.assert_awaited_once()
    notifications.publish.assert_awaited_once()


@pytest.mark.anyio
async def test_create_session_raises_when_project_not_found(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.get_by_id.return_value = None

    with pytest.raises(ProjectNotFoundError):
        await workflow.create_session(
            project_id=project_id,
            actor_id=actor_id,
            name="My Session",
            presentation_asset_version_id=uuid4(),
            supporting_document_version_ids=[uuid4()],
        )

    uow.sessions.create.assert_not_awaited()
    uow.manifests.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_create_session_raises_when_actor_is_not_member(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.get_by_id.return_value = MagicMock()
    uow.projects.is_member.return_value = False

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.create_session(
            project_id=project_id,
            actor_id=actor_id,
            name="My Session",
            presentation_asset_version_id=uuid4(),
            supporting_document_version_ids=[uuid4()],
        )

    uow.sessions.create.assert_not_awaited()
    uow.manifests.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_get_session_returns_session_when_authorized(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True

    result = await workflow.get_session(
        session_id=practice_session.id,
        actor_id=actor_id,
    )

    assert result is practice_session

    uow.sessions.get_by_id.assert_awaited_once_with(
        practice_session.id,
    )

    uow.projects.is_member.assert_awaited_once_with(
        project_id=practice_session.project_id,
        user_id=actor_id,
    )


@pytest.mark.anyio
async def test_get_session_raises_when_not_found(
    workflow: SessionWorkflow,
    uow: MagicMock,
    actor_id: UUID,
) -> None:
    session_id = uuid4()

    uow.sessions.get_by_id.return_value = None

    with pytest.raises(SessionNotFoundError):
        await workflow.get_session(
            session_id=session_id,
            actor_id=actor_id,
        )


@pytest.mark.anyio
async def test_get_session_raises_when_actor_is_not_member(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = False

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.get_session(
            session_id=practice_session.id,
            actor_id=actor_id,
        )


@pytest.mark.anyio
async def test_list_sessions_returns_sessions(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    sessions = [MagicMock(), MagicMock()]

    uow.projects.is_member.return_value = True
    uow.sessions.list_by_project.return_value = (
        sessions,
        "next-cursor",
    )

    result, cursor = await workflow.list_sessions(
        project_id=project_id,
        actor_id=actor_id,
        cursor="current-cursor",
        search="test",
        limit=10,
    )

    assert result == sessions
    assert cursor == "next-cursor"

    uow.sessions.list_by_project.assert_awaited_once_with(
        project_id=project_id,
        cursor="current-cursor",
        limit=10,
        search="test",
    )


@pytest.mark.anyio
async def test_list_sessions_raises_when_actor_is_not_member(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.is_member.return_value = False

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.list_sessions(
            project_id=project_id,
            actor_id=actor_id,
        )

    uow.sessions.list_by_project.assert_not_awaited()


@pytest.mark.anyio
async def test_update_session_updates_session_and_manifest(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    manifest.session_id = practice_session.id

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.manifests.get_by_session_id.return_value = manifest

    new_presentation_id = uuid4()
    new_document_id = uuid4()

    result = await workflow.update_session(
        session_id=practice_session.id,
        actor_id=actor_id,
        expected_version=1,
        name="Updated Session",
        presentation_version_id=new_presentation_id,
        supporting_document_version_ids=[new_document_id],
    )

    assert result.name == "Updated Session"
    assert manifest.presentation_version_id == new_presentation_id
    assert manifest.supporting_document_version_ids == [new_document_id]

    uow.sessions.update.assert_awaited_once_with(
        practice_session,
        expected_version=1,
    )
    uow.manifests.update.assert_awaited_once_with(manifest)
    uow.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_update_session_rejects_frozen_manifest(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    manifest.session_id = practice_session.id
    manifest.frozen_at = datetime.now(UTC)

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.manifests.get_by_session_id.return_value = manifest

    with pytest.raises(ManifestAlreadyFrozen):
        await workflow.update_session(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            name="Updated",
        )

    uow.sessions.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_get_analysis_attempts_returns_attempts(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    attempts = [MagicMock(), MagicMock()]

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_all_by_session_id.return_value = (
        attempts,
        "next-cursor",
    )

    result, cursor = await workflow.get_analysis_attempts(
        session_id=practice_session.id,
        actor_id=actor_id,
        cursor="cursor",
        limit=10,
    )

    assert result == attempts
    assert cursor == "next-cursor"

    uow.attempts.get_all_by_session_id.assert_awaited_once_with(
        session_id=practice_session.id,
        next_cursor="cursor",
        limit=10,
    )


@pytest.mark.anyio
async def test_retry_creates_new_attempt_after_failed_attempt(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    manifest.session_id = practice_session.id
    manifest.frozen_at = datetime.now(UTC)

    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    result = await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="retry-key",
    )

    assert isinstance(result, AnalysisAttempt)
    assert result.session_id == practice_session.id
    assert result.status == AnalysisAttemptStatus.QUEUED
    assert result.attempt_number == 2
    assert result.version == 1
    assert result.idempotency_key == "retry-key"

    uow.attempts.create.assert_awaited_once()
    uow.jobs.create.assert_awaited_once()
    uow.commit.assert_awaited_once()

    retried_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    assert retried_job.practice_session_id == practice_session.id
    assert retried_job.attempt_id == result.id
    assert retried_job.analysis_attempt == 2
    assert retried_job.status == AnalysisJobStatus.PENDING
    assert retried_job.job_type == "analyze_session"
    assert retried_job.payload is not None
    assert retried_job.payload["schema_version"] == 1
    assert retried_job.payload["analysis_attempt"] == 2
    assert retried_job.payload["payload"]["requested_capabilities"] == [
        "speech",
        "diarization",
        "vision",
        "audio",
        "documents",
        "questions",
    ]


@pytest.mark.anyio
async def test_retry_rejects_when_latest_attempt_is_not_failed(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    latest_attempt = MagicMock()
    latest_attempt.status = AnalysisAttemptStatus.COMPLETED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = latest_attempt

    with pytest.raises(RetryNotAllowed):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="retry-key",
        )

    uow.attempts.create.assert_not_awaited()


@pytest.mark.anyio
async def test_cancel_cancels_session_and_latest_attempt(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    manifest.session_id = practice_session.id
    frozen_time = datetime(2026, 9, 1, tzinfo=UTC)
    manifest.frozen_at = frozen_time

    attempt = MagicMock()
    attempt.status = AnalysisAttemptStatus.RUNNING
    attempt.version = 1

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_active.return_value = attempt

    result = await workflow.cancel(
        session_id=practice_session.id,
        actor_id=actor_id,
        reason="User cancelled",
    )

    assert result.status == SessionStatus.CANCELLED
    assert result.cancelled_at is not None
    assert result.cancelled_by == actor_id
    assert result.cancellation_reason == "User cancelled"

    attempt.transition_to.assert_called_once_with(
        AnalysisAttemptStatus.CANCELLED, result.cancelled_at
    )

    assert manifest.frozen_at == frozen_time

    uow.attempts.update.assert_awaited_once_with(attempt, expected_version=1)
    uow.sessions.update.assert_awaited_once()
    uow.manifests.update.assert_not_awaited()
    uow.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_rejects_completed_session(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.COMPLETED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True

    with pytest.raises(InvalidSessionStatusTransition):
        await workflow.cancel(
            session_id=practice_session.id,
            actor_id=actor_id,
            reason=None,
        )

    uow.sessions.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_create_analysis_attempt_success(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    result = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-start",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert result.session_id == practice_session.id
    assert result.attempt_number == 1
    assert result.status == AnalysisAttemptStatus.QUEUED
    assert result.idempotency_key == "key-start"
    assert practice_session.status == SessionStatus.ANALYZING
    assert practice_session.consent_granted is True
    assert practice_session.consent_policy_version == CURRENT_CONSENT_POLICY_VERSION
    assert practice_session.consent_confirmed_by == actor_id
    assert practice_session.started_at is not None
    assert manifest.frozen_at is not None

    uow.attempts.create.assert_awaited_once()
    uow.jobs.create.assert_awaited_once()
    uow.manifests.update.assert_awaited_once_with(manifest)
    uow.sessions.update.assert_awaited_once()
    uow.commit.assert_awaited_once()

    created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    assert created_job.practice_session_id == practice_session.id
    assert created_job.attempt_id == result.id
    assert created_job.analysis_attempt == 1
    assert created_job.status == AnalysisJobStatus.PENDING
    assert created_job.job_type == "analyze_session"
    assert created_job.payload is not None
    assert created_job.payload["schema_version"] == 1
    assert created_job.payload["job_type"] == "analyze_session"
    assert created_job.payload["practice_session_id"] == str(practice_session.id)
    assert created_job.payload["analysis_attempt"] == 1
    assert created_job.payload["trace_id"].startswith("trc_")
    assert "presentation" in created_job.payload["payload"]
    assert "rubric" in created_job.payload["payload"]
    assert created_job.payload["payload"]["requested_capabilities"] == [
        "speech",
        "diarization",
        "vision",
        "audio",
        "documents",
        "questions",
    ]


@pytest.mark.anyio
async def test_create_analysis_attempt_returns_existing_when_idempotent(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    start_hash = hashlib.sha256(f"{True}:{CURRENT_CONSENT_POLICY_VERSION}".encode()).hexdigest()
    existing_attempt = MagicMock(spec=AnalysisAttempt)
    existing_attempt.request_hash = start_hash
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = existing_attempt

    result = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="existing-key",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert result == existing_attempt
    uow.attempts.create.assert_not_awaited()
    uow.jobs.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("existing_hash", ["different_hash", None])
async def test_create_analysis_attempt_returns_conflict_when_idempotent_with_different_consent(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
    existing_hash: str | None,
) -> None:
    existing_attempt = MagicMock(spec=AnalysisAttempt)
    existing_attempt.request_hash = existing_hash
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = existing_attempt

    with pytest.raises(IdempotencyConflict, match="different parameters"):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="existing-key",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )


@pytest.mark.anyio
async def test_create_analysis_attempt_rejects_draft_session(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.DRAFT
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = None

    with pytest.raises(SessionNotReadyError):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-draft",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )


@pytest.mark.anyio
async def test_create_analysis_attempt_rejects_unverified_assets(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = False

    with pytest.raises(UnverifiedAsset):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-unverified",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )


@pytest.mark.anyio
async def test_create_analysis_attempt_rejects_missing_consent(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = None

    with pytest.raises(ConsentRequiredError):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-noconsent",
            consent_accepted=False,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )


@pytest.mark.anyio
async def test_create_analysis_attempt_rejects_outdated_consent_policy(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = None

    with pytest.raises(ConsentPolicyOutdated):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-outdated",
            consent_accepted=True,
            consent_policy_version=999,
        )


@pytest.mark.anyio
async def test_retry_returns_existing_when_idempotent(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    retry_hash = hashlib.sha256(b"retry").hexdigest()
    existing_attempt = MagicMock(spec=AnalysisAttempt)
    existing_attempt.request_hash = retry_hash
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = existing_attempt

    result = await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="retry-existing",
    )

    assert result == existing_attempt
    uow.attempts.create.assert_not_awaited()
    uow.jobs.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_retry_returns_conflict_when_idempotent_with_different_payload(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    existing_attempt = MagicMock(spec=AnalysisAttempt)
    existing_attempt.request_hash = "start_consent_hash"
    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_by_idempotency_key.return_value = existing_attempt

    with pytest.raises(IdempotencyConflict, match="different parameters"):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="reused-key",
        )


@pytest.mark.anyio
async def test_retry_rejects_when_session_not_failed(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED

    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_latest.return_value = failed_attempt

    with pytest.raises(RetryNotAllowed):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="retry-analyzing",
        )


@pytest.mark.anyio
async def test_retry_rejects_when_manifest_not_frozen(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest

    with pytest.raises(RetryNotAllowed):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="retry-unfrozen",
        )


@pytest.mark.anyio
async def test_cancel_rejects_unauthorized_user(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
) -> None:
    other_user = uuid4()
    practice_session.created_by = uuid4()
    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_owner.return_value = False

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.cancel(
            session_id=practice_session.id,
            actor_id=other_user,
            reason="Unauthorized",
        )


@pytest.mark.anyio
async def test_cancel_idempotent_when_already_cancelled(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.CANCELLED
    practice_session.cancelled_at = datetime.now(UTC)
    practice_session.cancelled_by = actor_id
    uow.sessions.get_by_id.return_value = practice_session

    result = await workflow.cancel(
        session_id=practice_session.id,
        actor_id=actor_id,
        reason="Duplicate cancel",
    )

    assert result.status == SessionStatus.CANCELLED
    uow.attempts.get_active.assert_not_awaited()
    uow.sessions.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_create_session_rejects_duplicate_documents(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.get_by_id.return_value = MagicMock()
    uow.projects.is_member.return_value = True
    dup_id = uuid4()

    with pytest.raises(InvalidManifestDocuments):
        await workflow.create_session(
            project_id=project_id,
            actor_id=actor_id,
            name="Session with dupes",
            presentation_asset_version_id=uuid4(),
            supporting_document_version_ids=[dup_id, dup_id],
        )


@pytest.mark.anyio
async def test_create_session_rejects_more_than_five_documents(
    workflow: SessionWorkflow,
    uow: MagicMock,
    project_id: UUID,
    actor_id: UUID,
) -> None:
    uow.projects.get_by_id.return_value = MagicMock()
    uow.projects.is_member.return_value = True

    with pytest.raises(InvalidManifestDocuments):
        await workflow.create_session(
            project_id=project_id,
            actor_id=actor_id,
            name="Session with 6 docs",
            presentation_asset_version_id=uuid4(),
            supporting_document_version_ids=[uuid4() for _ in range(6)],
        )


@pytest.mark.anyio
async def test_update_session_rejects_duplicate_documents(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.projects.is_member.return_value = True
    dup_id = uuid4()

    with pytest.raises(InvalidManifestDocuments):
        await workflow.update_session(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            supporting_document_version_ids=[dup_id, dup_id],
        )


@pytest.mark.anyio
async def test_update_session_rejects_more_than_five_documents(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.projects.is_member.return_value = True

    with pytest.raises(InvalidManifestDocuments):
        await workflow.update_session(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            supporting_document_version_ids=[uuid4() for _ in range(6)],
        )


@pytest.mark.anyio
async def test_update_speaker_mappings_success(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    practice_session.version = 1
    uow.sessions.get_by_id.return_value = practice_session
    attempt = MagicMock(spec=AnalysisAttempt)
    attempt.id = uuid4()
    uow.attempts.get_current_by_session_id.return_value = attempt
    uow.projects.is_member.return_value = True

    user_a = uuid4()
    team_member_id = uuid4()
    uow.projects.get_team_member_id.return_value = team_member_id

    mappings = [
        SpeakerMapping(
            id=uuid4(),
            attempt_id=attempt.id,
            speaker_label="SPEAKER_00",
            member_id=None,
            mapped_by=actor_id,
            mapped_at=datetime.now(UTC),
            user_id=user_a,
        )
    ]

    result = await workflow.update_speaker_mappings(
        session_id=practice_session.id,
        actor_id=actor_id,
        expected_version=1,
        mappings=mappings,
    )

    assert len(result) == 1
    assert result[0].speaker_label == "SPEAKER_00"
    assert result[0].member_id == team_member_id
    assert result[0].user_id == user_a
    uow.speaker_mappings.create.assert_awaited_once()
    created_mapping = uow.speaker_mappings.create.call_args[0][0]
    assert created_mapping.member_id == team_member_id
    assert created_mapping.user_id == user_a
    uow.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_update_speaker_mappings_rejects_stale_etag(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    practice_session.version = 2
    uow.sessions.get_by_id.return_value = practice_session

    with pytest.raises(StaleEntityVersion):
        await workflow.update_speaker_mappings(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            mappings=[],
        )


@pytest.mark.anyio
async def test_update_speaker_mappings_rejects_unknown_label(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    practice_session.version = 1
    uow.sessions.get_by_id.return_value = practice_session
    attempt = MagicMock(spec=AnalysisAttempt)
    attempt.id = uuid4()
    uow.attempts.get_current_by_session_id.return_value = attempt

    mappings = [
        SpeakerMapping(
            id=uuid4(),
            attempt_id=attempt.id,
            speaker_label="UNKNOWN_LABEL",
            member_id=actor_id,
            mapped_by=actor_id,
            mapped_at=datetime.now(UTC),
            user_id=actor_id,
        )
    ]

    with pytest.raises(InvalidSpeakerLabel):
        await workflow.update_speaker_mappings(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            mappings=mappings,
        )


@pytest.mark.anyio
async def test_update_speaker_mappings_rejects_non_team_member(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    practice_session.version = 1
    uow.sessions.get_by_id.return_value = practice_session
    attempt = MagicMock(spec=AnalysisAttempt)
    attempt.id = uuid4()
    uow.attempts.get_current_by_session_id.return_value = attempt
    uow.projects.is_member.return_value = True
    uow.projects.get_team_member_id.return_value = None

    non_member_id = uuid4()
    mappings = [
        SpeakerMapping(
            id=uuid4(),
            attempt_id=attempt.id,
            speaker_label="SPEAKER_00",
            member_id=None,
            mapped_by=actor_id,
            mapped_at=datetime.now(UTC),
            user_id=non_member_id,
        )
    ]

    with pytest.raises(InvalidTeamMember):
        await workflow.update_speaker_mappings(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            mappings=mappings,
        )


@pytest.mark.anyio
async def test_update_speaker_mappings_raises_analysis_not_ready_when_labels_empty(
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    workflow_default_reader = SessionWorkflow(uow)
    practice_session.status = SessionStatus.ANALYZING
    practice_session.version = 1
    uow.sessions.get_by_id.return_value = practice_session
    attempt = MagicMock(spec=AnalysisAttempt)
    attempt.id = uuid4()
    uow.attempts.get_current_by_session_id.return_value = attempt
    uow.projects.is_member.return_value = True

    mappings = [
        SpeakerMapping(
            id=uuid4(),
            attempt_id=attempt.id,
            speaker_label="SPEAKER_00",
            member_id=None,
            mapped_by=actor_id,
            mapped_at=datetime.now(UTC),
            user_id=uuid4(),
        )
    ]

    with pytest.raises(AnalysisNotReady, match="Diarization results are not available"):
        await workflow_default_reader.update_speaker_mappings(
            session_id=practice_session.id,
            actor_id=actor_id,
            expected_version=1,
            mappings=mappings,
        )


@pytest.mark.anyio
async def test_create_analysis_attempt_fails_when_snapshot_missing(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None
    manifest.snapshot = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True
    uow.projects.get_asset_version_snapshots.side_effect = None
    uow.projects.get_asset_version_snapshots.return_value = {}

    with pytest.raises(UnverifiedAsset, match="Missing verified asset snapshot"):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-missing-snapshot",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )


@pytest.mark.anyio
async def test_cancel_with_idempotency_conflict(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    uow.sessions.get_by_id.return_value = practice_session
    prior_record = MagicMock()
    prior_record.request_hash = "different_hash"
    uow.idempotency.get.return_value = prior_record

    with pytest.raises(IdempotencyConflict):
        await workflow.cancel(
            session_id=practice_session.id,
            actor_id=actor_id,
            reason="new reason",
            idempotency_key="reused-key",
        )
    uow.idempotency.get.assert_awaited_once_with(
        practice_session.id,
        actor_id,
        "cancel",
        "reused-key",
    )


@pytest.mark.anyio
async def test_create_analysis_attempt_concurrent_same_key_returns_winner(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.projects.asset_versions_are_verified.return_value = True

    winning_attempt = MagicMock(spec=AnalysisAttempt)
    winning_attempt.request_hash = hashlib.sha256(
        f"{True}:{CURRENT_CONSENT_POLICY_VERSION}".encode()
    ).hexdigest()
    uow.attempts.get_by_idempotency_key.side_effect = [None, winning_attempt]
    uow.attempts.create.side_effect = IdempotencyConflict("duplicate key")

    result = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="concurrent-key",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert result == winning_attempt
    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_create_analysis_attempt_concurrent_different_key_raises_conflict(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.projects.asset_versions_are_verified.return_value = True

    uow.attempts.get_by_idempotency_key.side_effect = [None, None]
    uow.attempts.create.side_effect = IdempotencyConflict("duplicate attempt")

    with pytest.raises(IdempotencyConflict, match="created concurrently"):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="conflicted-key",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )

    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_retry_concurrent_same_key_returns_winner(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    failed_attempt = MagicMock(spec=AnalysisAttempt)
    failed_attempt.status = AnalysisAttemptStatus.FAILED
    manifest.frozen_at = datetime.now(UTC)

    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    winning_attempt = MagicMock(spec=AnalysisAttempt)
    winning_attempt.request_hash = hashlib.sha256(b"retry").hexdigest()
    uow.attempts.get_by_idempotency_key.side_effect = [None, winning_attempt]
    uow.attempts.create.side_effect = IdempotencyConflict("duplicate retry")

    result = await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="retry-concurrent-key",
    )

    assert result == winning_attempt
    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_retry_concurrent_different_key_raises_conflict(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    failed_attempt = MagicMock(spec=AnalysisAttempt)
    failed_attempt.status = AnalysisAttemptStatus.FAILED
    manifest.frozen_at = datetime.now(UTC)

    uow.sessions.get_by_id.return_value = practice_session
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    uow.attempts.get_by_idempotency_key.side_effect = [None, None]
    uow.attempts.create.side_effect = IdempotencyConflict("duplicate retry")

    with pytest.raises(IdempotencyConflict, match="created concurrently"):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="retry-conflicted-key",
        )

    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_concurrent_race_replays_safely_when_session_cancelled(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    cancelled_session = MagicMock(spec=PracticeSession)
    cancelled_session.status = SessionStatus.CANCELLED

    uow.sessions.get_by_id.side_effect = [practice_session, cancelled_session]
    uow.idempotency.get.return_value = None
    uow.attempts.get_active.return_value = None
    uow.sessions.update.side_effect = StaleEntityVersion("concurrent update")

    result = await workflow.cancel(
        session_id=practice_session.id,
        actor_id=actor_id,
        reason="race reason",
        idempotency_key="cancel-race-key",
    )

    assert result == cancelled_session
    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_update_session_transitions_draft_to_ready(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.DRAFT
    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.projects.is_member.return_value = True

    result = await workflow.update_session(
        session_id=practice_session.id,
        actor_id=actor_id,
        expected_version=1,
        name="Updated Name",
    )

    assert result.status == SessionStatus.READY
    uow.sessions.update.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_concurrent_race_with_active_attempt_stale_version_returns_cancelled(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.ANALYZING
    active_attempt = MagicMock(spec=AnalysisAttempt)
    active_attempt.version = 1

    cancelled_session = MagicMock(spec=PracticeSession)
    cancelled_session.status = SessionStatus.CANCELLED

    uow.sessions.get_by_id.side_effect = [practice_session, cancelled_session]
    uow.idempotency.get.return_value = None
    uow.attempts.get_active.return_value = active_attempt
    uow.attempts.update.side_effect = StaleEntityVersion("active attempt updated concurrently")

    result = await workflow.cancel(
        session_id=practice_session.id,
        actor_id=actor_id,
        reason="concurrent cancel",
        idempotency_key="cancel-active-race-key",
    )

    assert result == cancelled_session
    uow.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_create_analysis_attempt_produces_contract_valid_message(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="contract-valid-start",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    schema_path = (
        Path(__file__).resolve().parent.parent / "contracts" / "schemas" / "ai_job.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(created_job.payload)

    parsed = AIJobQueueMessage.model_validate(created_job.payload)
    assert parsed.schema_version == 1
    assert parsed.job_type == AIJobType.ANALYZE_SESSION
    assert parsed.practice_session_id == str(practice_session.id)
    assert parsed.analysis_attempt == 1
    assert parsed.trace_id.startswith("trc_")
    assert isinstance(parsed.payload, AnalyzeSessionPayload)
    assert parsed.payload.rubric.rubric_id == "startup_pitch"
    assert parsed.payload.rubric.version == 1
    assert parsed.payload.presentation.artifact_id is not None
    assert parsed.payload.presentation.object_key is not None
    assert parsed.payload.presentation.checksum.startswith("sha256:")
    assert parsed.payload.presentation.media_type is not None
    assert parsed.payload.requested_capabilities == [
        "speech",
        "diarization",
        "vision",
        "audio",
        "documents",
        "questions",
    ]


@pytest.mark.anyio
async def test_retry_produces_contract_valid_message(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    manifest.session_id = practice_session.id
    manifest.frozen_at = datetime.now(UTC)

    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="contract-valid-retry",
    )

    retried_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    schema_path = (
        Path(__file__).resolve().parent.parent / "contracts" / "schemas" / "ai_job.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(retried_job.payload)

    parsed = AIJobQueueMessage.model_validate(retried_job.payload)
    assert parsed.schema_version == 1
    assert parsed.job_type == AIJobType.ANALYZE_SESSION
    assert parsed.practice_session_id == str(practice_session.id)
    assert parsed.analysis_attempt == 2
    assert parsed.trace_id.startswith("trc_")


@pytest.mark.anyio
async def test_create_analysis_attempt_dispatches_job_to_queue_after_commit(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    queue = FakeAIJobQueue()
    workflow = SessionWorkflow(uow, queue=queue)

    async def _mock_change_queued(jid: UUID, now_dt: datetime) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.status = AnalysisJobStatus.QUEUED
        created_job.queued_at = now_dt
        created_job.updated_at = now_dt
        return created_job

    uow.jobs.change_pending_to_queued.side_effect = _mock_change_queued

    result = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-dispatch-success",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert result.session_id == practice_session.id
    assert queue.count == 1
    enqueued = queue.last_message
    assert enqueued is not None
    assert str(enqueued.job_id) == str(uow.jobs.create.call_args[0][0].id)
    assert enqueued.practice_session_id == str(practice_session.id)
    assert enqueued.analysis_attempt == 1

    uow.jobs.change_pending_to_queued.assert_awaited_once()
    assert uow.commit.await_count == 2
    created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    assert created_job.status == AnalysisJobStatus.QUEUED


@pytest.mark.anyio
async def test_create_analysis_attempt_call_ordering_database_commits_before_queue(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    call_order: list[str] = []

    async def _logging_commit() -> None:
        call_order.append("db_commit")

    uow.commit.side_effect = _logging_commit

    class LoggingFakeAIJobQueue(FakeAIJobQueue):
        async def enqueue(self, *args: Any, **kwargs: Any) -> Any:
            call_order.append("queue_enqueue")
            return await super().enqueue(*args, **kwargs)

    queue = LoggingFakeAIJobQueue()
    workflow = SessionWorkflow(uow, queue=queue)

    async def _mock_change_queued(jid: UUID, now_dt: datetime) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.status = AnalysisJobStatus.QUEUED
        return created_job

    uow.jobs.change_pending_to_queued.side_effect = _mock_change_queued

    await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-call-ordering",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert call_order == ["db_commit", "queue_enqueue", "db_commit"]


@pytest.mark.anyio
async def test_create_analysis_attempt_does_not_call_queue_if_database_commit_fails(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True
    uow.commit.side_effect = IdempotencyConflict("Simulated commit conflict")

    queue = FakeAIJobQueue()
    workflow = SessionWorkflow(uow, queue=queue)

    with pytest.raises(IdempotencyConflict):
        await workflow.create_analysis_attempt(
            session_id=practice_session.id,
            actor_id=actor_id,
            idempotency_key="key-commit-fails",
            consent_accepted=True,
            consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
        )

    assert queue.count == 0
    uow.jobs.change_pending_to_queued.assert_not_awaited()


@pytest.mark.anyio
async def test_create_analysis_attempt_failure_window_retains_recoverable_pending_job(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    queue = FakeAIJobQueue()
    queue.fail_next(
        1,
        AIJobQueueTemporaryFailure("Redis connection timeout", retry_after_seconds=45.0),
    )

    async def _mock_record_failure(
        jid: UUID,
        now_dt: datetime,
        next_at: datetime,
        cat: str,
        msg: str | None = None,
    ) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.dispatch_retry_count += 1
        created_job.next_dispatch_at = next_at
        created_job.last_dispatch_error_category = cat
        created_job.last_error = msg
        created_job.updated_at = now_dt
        return created_job

    uow.jobs.record_dispatch_failure.side_effect = _mock_record_failure

    workflow = SessionWorkflow(uow, queue=queue)

    attempt = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-failure-window",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )

    assert attempt is not None
    assert attempt.attempt_number == 1
    assert uow.commit.await_count == 2
    uow.jobs.change_pending_to_queued.assert_not_awaited()
    uow.jobs.record_dispatch_failure.assert_awaited_once()

    call_args = uow.jobs.record_dispatch_failure.call_args[0]
    assert call_args[3] == "temporary_queue_failure"

    created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    assert created_job.status == AnalysisJobStatus.PENDING
    assert created_job.dispatch_retry_count == 1
    assert created_job.last_dispatch_error_category == "temporary_queue_failure"
    assert created_job.next_dispatch_at is not None


@pytest.mark.anyio
async def test_create_analysis_attempt_idempotent_replay_does_not_re_dispatch(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.READY
    manifest.session_id = practice_session.id
    manifest.frozen_at = None

    uow.sessions.get_by_id.return_value = practice_session
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_by_idempotency_key.return_value = None
    uow.projects.asset_versions_are_verified.return_value = True

    queue = FakeAIJobQueue()
    workflow = SessionWorkflow(uow, queue=queue)

    start_hash = hashlib.sha256(f"{True}:{CURRENT_CONSENT_POLICY_VERSION}".encode()).hexdigest()

    async def _mock_change_queued(jid: UUID, now_dt: datetime) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.status = AnalysisJobStatus.QUEUED
        return created_job

    uow.jobs.change_pending_to_queued.side_effect = _mock_change_queued

    first_attempt = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-idempotent-replay",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )
    assert queue.count == 1

    first_attempt.request_hash = start_hash
    uow.attempts.get_by_idempotency_key.return_value = first_attempt

    second_attempt = await workflow.create_analysis_attempt(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-idempotent-replay",
        consent_accepted=True,
        consent_policy_version=CURRENT_CONSENT_POLICY_VERSION,
    )
    assert second_attempt.id == first_attempt.id
    assert queue.count == 1


@pytest.mark.anyio
async def test_retry_analysis_attempt_dispatches_job_to_queue_after_commit(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    manifest.session_id = practice_session.id
    manifest.frozen_at = datetime.now(UTC)

    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    queue = FakeAIJobQueue()
    workflow = SessionWorkflow(uow, queue=queue)

    async def _mock_change_queued(jid: UUID, now_dt: datetime) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.status = AnalysisJobStatus.QUEUED
        return created_job

    uow.jobs.change_pending_to_queued.side_effect = _mock_change_queued

    result = await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-retry-dispatch",
    )

    assert result.attempt_number == 2
    assert queue.count == 1
    enqueued = queue.last_message
    assert enqueued is not None
    assert enqueued.analysis_attempt == 2
    assert uow.commit.await_count == 2
    uow.jobs.change_pending_to_queued.assert_awaited_once()


@pytest.mark.anyio
async def test_retry_analysis_attempt_call_ordering_and_failure_window(
    uow: MagicMock,
    practice_session: PracticeSession,
    manifest: SessionManifest,
    actor_id: UUID,
) -> None:
    practice_session.status = SessionStatus.FAILED
    manifest.session_id = practice_session.id
    manifest.frozen_at = datetime.now(UTC)

    failed_attempt = MagicMock()
    failed_attempt.status = AnalysisAttemptStatus.FAILED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = failed_attempt
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_next_attempt_number.return_value = 2

    call_order: list[str] = []

    async def _logging_commit() -> None:
        call_order.append("db_commit")

    uow.commit.side_effect = _logging_commit

    class LoggingFakeAIJobQueue(FakeAIJobQueue):
        async def enqueue(self, *args: Any, **kwargs: Any) -> Any:
            call_order.append("queue_enqueue")
            return await super().enqueue(*args, **kwargs)

    queue = LoggingFakeAIJobQueue()
    queue.fail_next(1, AIJobQueueTemporaryFailure("Celery broker error"))

    workflow = SessionWorkflow(uow, queue=queue)

    async def _mock_record_failure(
        jid: UUID,
        now_dt: datetime,
        next_at: datetime,
        cat: str,
        msg: str | None = None,
    ) -> AnalysisJob:
        created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
        created_job.dispatch_retry_count += 1
        created_job.last_dispatch_error_category = cat
        return created_job

    uow.jobs.record_dispatch_failure.side_effect = _mock_record_failure

    attempt = await workflow.retry(
        session_id=practice_session.id,
        actor_id=actor_id,
        idempotency_key="key-retry-fail-window",
    )

    assert call_order == ["db_commit", "queue_enqueue", "db_commit"]
    assert attempt.attempt_number == 2
    uow.jobs.record_dispatch_failure.assert_awaited_once()
    created_job: AnalysisJob = uow.jobs.create.call_args[0][0]
    assert created_job.status == AnalysisJobStatus.PENDING
    assert created_job.dispatch_retry_count == 1


@pytest.mark.anyio
async def test_ai_jobs_dispatch_preconditions_and_safety(
    uow: MagicMock,
) -> None:
    queue = FakeAIJobQueue()
    ai_jobs = AIJobs(uow, queue=queue)

    now = datetime.now(UTC)
    job_completed = AnalysisJob(
        id=uuid4(),
        practice_session_id=uuid4(),
        attempt_id=uuid4(),
        analysis_attempt=1,
        job_type="analyze_session",
        status=AnalysisJobStatus.COMPLETED,
        correlation_id=uuid4(),
        last_update_sequence=1,
        payload_version=1,
        attempts=1,
        cancel_requested=False,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
        payload={"schema_version": 1},
    )
    assert await ai_jobs.dispatch(job_completed) is False
    assert queue.count == 0

    job_cancelled = AnalysisJob(
        id=uuid4(),
        practice_session_id=uuid4(),
        attempt_id=uuid4(),
        analysis_attempt=1,
        job_type="analyze_session",
        status=AnalysisJobStatus.PENDING,
        correlation_id=uuid4(),
        last_update_sequence=0,
        payload_version=1,
        attempts=0,
        cancel_requested=True,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        payload={"schema_version": 1},
    )
    assert await ai_jobs.dispatch(job_cancelled) is False
    assert queue.count == 0

    ai_jobs_no_queue = AIJobs(uow, queue=None)
    job_pending = AnalysisJob(
        id=uuid4(),
        practice_session_id=uuid4(),
        attempt_id=uuid4(),
        analysis_attempt=1,
        job_type="analyze_session",
        status=AnalysisJobStatus.PENDING,
        correlation_id=uuid4(),
        last_update_sequence=0,
        payload_version=1,
        attempts=0,
        cancel_requested=False,
        retry_count=0,
        last_error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        payload={"schema_version": 1},
    )
    assert await ai_jobs_no_queue.dispatch(job_pending) is False
    assert queue.count == 0
