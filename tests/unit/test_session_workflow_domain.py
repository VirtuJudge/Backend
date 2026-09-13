from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    InvalidAttemptState,
    InvalidSessionStatusTransition,
)


def _create_practice_session(status: SessionStatus = SessionStatus.DRAFT) -> PracticeSession:
    now = datetime.now(UTC)
    return PracticeSession(
        id=uuid4(),
        project_id=uuid4(),
        created_by=uuid4(),
        name="Test Session",
        status=status,
        version=1,
        created_at=now,
        updated_at=now,
        consent_granted=True,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
    )


def _create_analysis_attempt(
    status: AnalysisAttemptStatus = AnalysisAttemptStatus.QUEUED,
) -> AnalysisAttempt:
    now = datetime.now(UTC)
    return AnalysisAttempt(
        id=uuid4(),
        session_id=uuid4(),
        manifest_id=uuid4(),
        idempotency_key="key-1",
        attempt_number=1,
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


class TestPracticeSessionTransitions:
    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (SessionStatus.DRAFT, SessionStatus.READY),
            (SessionStatus.DRAFT, SessionStatus.CANCELLED),
            (SessionStatus.READY, SessionStatus.ANALYZING),
            (SessionStatus.READY, SessionStatus.CANCELLED),
            (SessionStatus.ANALYZING, SessionStatus.QUESTIONS_READY),
            (SessionStatus.ANALYZING, SessionStatus.FAILED),
            (SessionStatus.ANALYZING, SessionStatus.CANCELLED),
            (SessionStatus.FAILED, SessionStatus.ANALYZING),
            (SessionStatus.FAILED, SessionStatus.CANCELLED),
            (SessionStatus.QUESTIONS_READY, SessionStatus.QUESTIONS_IN_PROGRESS),
            (SessionStatus.QUESTIONS_READY, SessionStatus.CANCELLED),
            (SessionStatus.QUESTIONS_IN_PROGRESS, SessionStatus.REPORT_GENERATING),
            (SessionStatus.QUESTIONS_IN_PROGRESS, SessionStatus.FAILED),
            (SessionStatus.QUESTIONS_IN_PROGRESS, SessionStatus.CANCELLED),
            (SessionStatus.REPORT_GENERATING, SessionStatus.COMPLETED),
            (SessionStatus.REPORT_GENERATING, SessionStatus.FAILED),
            (SessionStatus.REPORT_GENERATING, SessionStatus.CANCELLED),
        ],
    )
    def test_allowed_transitions(
        self, from_status: SessionStatus, to_status: SessionStatus
    ) -> None:
        session = _create_practice_session(status=from_status)
        session.transition_to(to_status)
        assert session.status == to_status

    @pytest.mark.parametrize("status", list(SessionStatus))
    def test_same_status_transition_is_noop(self, status: SessionStatus) -> None:
        session = _create_practice_session(status=status)
        session.transition_to(status)
        assert session.status == status

    @pytest.mark.parametrize(
        ("from_status", "forbidden_target"),
        [
            (SessionStatus.DRAFT, SessionStatus.ANALYZING),
            (SessionStatus.DRAFT, SessionStatus.COMPLETED),
            (SessionStatus.READY, SessionStatus.DRAFT),
            (SessionStatus.READY, SessionStatus.COMPLETED),
            (SessionStatus.ANALYZING, SessionStatus.DRAFT),
            (SessionStatus.ANALYZING, SessionStatus.COMPLETED),
            (SessionStatus.FAILED, SessionStatus.READY),
            (SessionStatus.FAILED, SessionStatus.COMPLETED),
            (SessionStatus.QUESTIONS_READY, SessionStatus.ANALYZING),
            (SessionStatus.QUESTIONS_READY, SessionStatus.COMPLETED),
            (SessionStatus.QUESTIONS_IN_PROGRESS, SessionStatus.ANALYZING),
            (SessionStatus.QUESTIONS_IN_PROGRESS, SessionStatus.COMPLETED),
            (SessionStatus.REPORT_GENERATING, SessionStatus.ANALYZING),
            (SessionStatus.REPORT_GENERATING, SessionStatus.DRAFT),
            (SessionStatus.COMPLETED, SessionStatus.DRAFT),
            (SessionStatus.COMPLETED, SessionStatus.READY),
            (SessionStatus.COMPLETED, SessionStatus.ANALYZING),
            (SessionStatus.COMPLETED, SessionStatus.CANCELLED),
            (SessionStatus.CANCELLED, SessionStatus.DRAFT),
            (SessionStatus.CANCELLED, SessionStatus.READY),
            (SessionStatus.CANCELLED, SessionStatus.ANALYZING),
        ],
    )
    def test_forbidden_transitions_raise(
        self, from_status: SessionStatus, forbidden_target: SessionStatus
    ) -> None:
        session = _create_practice_session(status=from_status)
        with pytest.raises(InvalidSessionStatusTransition):
            session.transition_to(forbidden_target)

    def test_cancel_sets_audit_fields(self) -> None:
        session = _create_practice_session(status=SessionStatus.READY)
        actor = uuid4()
        now = datetime.now(UTC)

        session.cancel(actor_id=actor, reason="Stop session", at=now)

        assert session.status == SessionStatus.CANCELLED
        assert session.cancelled_by == actor
        assert session.cancellation_reason == "Stop session"
        assert session.cancelled_at == now
        assert session.updated_at == now

    def test_cancel_idempotent_when_already_cancelled(self) -> None:
        session = _create_practice_session(status=SessionStatus.CANCELLED)
        initial_time = datetime.now(UTC)
        initial_actor = uuid4()
        session.cancelled_at = initial_time
        session.cancelled_by = initial_actor
        session.cancellation_reason = "Original reason"

        new_actor = uuid4()
        session.cancel(actor_id=new_actor, reason="New reason", at=datetime.now(UTC))

        assert session.status == SessionStatus.CANCELLED
        assert session.cancelled_by == initial_actor
        assert session.cancellation_reason == "Original reason"
        assert session.cancelled_at == initial_time

    def test_cancel_raises_when_in_completed_state(self) -> None:
        session = _create_practice_session(status=SessionStatus.COMPLETED)
        with pytest.raises(InvalidSessionStatusTransition):
            session.cancel(actor_id=uuid4(), reason="Too late", at=datetime.now(UTC))


class TestAnalysisAttemptTransitions:
    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (AnalysisAttemptStatus.QUEUED, AnalysisAttemptStatus.RUNNING),
            (AnalysisAttemptStatus.QUEUED, AnalysisAttemptStatus.FAILED),
            (AnalysisAttemptStatus.QUEUED, AnalysisAttemptStatus.CANCELLED),
            (AnalysisAttemptStatus.RUNNING, AnalysisAttemptStatus.COMPLETED),
            (AnalysisAttemptStatus.RUNNING, AnalysisAttemptStatus.FAILED),
            (AnalysisAttemptStatus.RUNNING, AnalysisAttemptStatus.CANCELLED),
        ],
    )
    def test_allowed_transitions(
        self, from_status: AnalysisAttemptStatus, to_status: AnalysisAttemptStatus
    ) -> None:
        attempt = _create_analysis_attempt(status=from_status)
        now = datetime.now(UTC)
        attempt.transition_to(to_status, at=now)
        assert attempt.status == to_status

    @pytest.mark.parametrize("status", list(AnalysisAttemptStatus))
    def test_same_status_transition_is_noop(self, status: AnalysisAttemptStatus) -> None:
        attempt = _create_analysis_attempt(status=status)
        now = datetime.now(UTC)
        attempt.transition_to(status, at=now)
        assert attempt.status == status

    @pytest.mark.parametrize(
        ("from_status", "forbidden_target"),
        [
            (AnalysisAttemptStatus.QUEUED, AnalysisAttemptStatus.COMPLETED),
            (AnalysisAttemptStatus.RUNNING, AnalysisAttemptStatus.QUEUED),
            (AnalysisAttemptStatus.COMPLETED, AnalysisAttemptStatus.QUEUED),
            (AnalysisAttemptStatus.COMPLETED, AnalysisAttemptStatus.RUNNING),
            (AnalysisAttemptStatus.COMPLETED, AnalysisAttemptStatus.FAILED),
            (AnalysisAttemptStatus.COMPLETED, AnalysisAttemptStatus.CANCELLED),
            (AnalysisAttemptStatus.FAILED, AnalysisAttemptStatus.QUEUED),
            (AnalysisAttemptStatus.FAILED, AnalysisAttemptStatus.RUNNING),
            (AnalysisAttemptStatus.FAILED, AnalysisAttemptStatus.COMPLETED),
            (AnalysisAttemptStatus.FAILED, AnalysisAttemptStatus.CANCELLED),
            (AnalysisAttemptStatus.CANCELLED, AnalysisAttemptStatus.QUEUED),
            (AnalysisAttemptStatus.CANCELLED, AnalysisAttemptStatus.RUNNING),
            (AnalysisAttemptStatus.CANCELLED, AnalysisAttemptStatus.COMPLETED),
            (AnalysisAttemptStatus.CANCELLED, AnalysisAttemptStatus.FAILED),
        ],
    )
    def test_forbidden_transitions_raise(
        self, from_status: AnalysisAttemptStatus, forbidden_target: AnalysisAttemptStatus
    ) -> None:
        attempt = _create_analysis_attempt(status=from_status)
        now = datetime.now(UTC)
        with pytest.raises(InvalidAttemptState):
            attempt.transition_to(forbidden_target, at=now)

    def test_transition_updates_respective_timestamps(self) -> None:
        now = datetime.now(UTC)

        attempt_running = _create_analysis_attempt(status=AnalysisAttemptStatus.QUEUED)
        attempt_running.transition_to(AnalysisAttemptStatus.RUNNING, at=now)
        assert attempt_running.started_at == now

        attempt_completed = _create_analysis_attempt(status=AnalysisAttemptStatus.RUNNING)
        attempt_completed.transition_to(AnalysisAttemptStatus.COMPLETED, at=now)
        assert attempt_completed.completed_at == now

        attempt_failed = _create_analysis_attempt(status=AnalysisAttemptStatus.RUNNING)
        attempt_failed.transition_to(AnalysisAttemptStatus.FAILED, at=now)
        assert attempt_failed.failed_at == now

        attempt_cancelled = _create_analysis_attempt(status=AnalysisAttemptStatus.RUNNING)
        attempt_cancelled.transition_to(AnalysisAttemptStatus.CANCELLED, at=now)
        assert attempt_cancelled.cancelled_at == now

    def test_analysis_attempt_has_optional_request_hash(self) -> None:
        attempt = _create_analysis_attempt()
        assert attempt.request_hash is None
        attempt_with_hash = AnalysisAttempt(
            id=uuid4(),
            session_id=uuid4(),
            manifest_id=uuid4(),
            idempotency_key="key",
            attempt_number=1,
            status=AnalysisAttemptStatus.QUEUED,
            failure_code=None,
            failure_message=None,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=None,
            failed_at=None,
            cancelled_at=None,
            version=1,
            request_hash="hash123",
        )
        assert attempt_with_hash.request_hash == "hash123"
