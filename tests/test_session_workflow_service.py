from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.application.session_workflow import SessionWorkflow
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    InvalidSessionStatusTransition,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    UnauthorizedSessionAction,
)


@pytest.fixture
def uow() -> MagicMock:
    uow = MagicMock()

    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    uow.projects = MagicMock()
    uow.sessions = MagicMock()
    uow.manifests = MagicMock()
    uow.attempts = MagicMock()

    uow.projects.get_by_id = AsyncMock()
    uow.projects.is_member = AsyncMock()

    uow.sessions.get_by_id = AsyncMock()
    uow.sessions.create = AsyncMock()
    uow.sessions.update = AsyncMock()
    uow.sessions.list_by_project = AsyncMock()

    uow.manifests.get_by_session_id = AsyncMock()
    uow.manifests.create = AsyncMock()
    uow.manifests.update = AsyncMock()

    uow.attempts.get_by_idempotency_key = AsyncMock()
    uow.attempts.get_next_attempt_number = AsyncMock()
    uow.attempts.get_all_by_session_id = AsyncMock()
    uow.attempts.get_latest = AsyncMock()
    uow.attempts.create = AsyncMock()
    uow.attempts.update = AsyncMock()

    uow.commit = AsyncMock()

    return uow


@pytest.fixture
def workflow(uow: MagicMock) -> SessionWorkflow:
    return SessionWorkflow(uow)


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
        document_version_id=uuid4(),
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
        document_version_id=document_version_id,
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
            document_version_id=uuid4(),
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
            document_version_id=uuid4(),
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
        document_version_id=new_document_id,
    )

    assert result.name == "Updated Session"
    assert manifest.presentation_version_id == new_presentation_id
    assert manifest.document_version_id == new_document_id

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

    result, cursor = await workflow.get_analysis_attemptss(
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
    manifest.session_id = practice_session.id

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
    )

    assert isinstance(result, AnalysisAttempt)
    assert result.session_id == practice_session.id
    assert result.status == AnalysisAttemptStatus.PENDING
    assert result.attempt_number == 2
    assert result.version == 1
    assert result.idempotency_key is None

    uow.attempts.create.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_retry_rejects_when_latest_attempt_is_not_failed(
    workflow: SessionWorkflow,
    uow: MagicMock,
    practice_session: PracticeSession,
    actor_id: UUID,
) -> None:
    latest_attempt = MagicMock()
    latest_attempt.status = AnalysisAttemptStatus.COMPLETED

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.attempts.get_latest.return_value = latest_attempt

    with pytest.raises(RetryNotAllowed):
        await workflow.retry(
            session_id=practice_session.id,
            actor_id=actor_id,
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
    manifest.session_id = practice_session.id

    attempt = MagicMock()
    attempt.status = AnalysisAttemptStatus.PENDING
    attempt.version = 1

    uow.sessions.get_by_id.return_value = practice_session
    uow.projects.is_member.return_value = True
    uow.manifests.get_by_session_id.return_value = manifest
    uow.attempts.get_latest.return_value = attempt

    result = await workflow.cancel(
        session_id=practice_session.id,
        actor_id=actor_id,
        reason="User cancelled",
    )

    assert result.status == SessionStatus.CANCELLED
    assert result.cancelled_at is not None
    assert result.consent_granted is False

    assert attempt.status == AnalysisAttemptStatus.CANCELLED
    assert attempt.cancelled_at is not None

    assert manifest.frozen_at is None

    uow.attempts.update.assert_awaited_once_with(attempt, expected_version=1)
    uow.sessions.update.assert_awaited_once()
    uow.manifests.update.assert_awaited()
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
