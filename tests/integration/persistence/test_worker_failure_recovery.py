from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ai_job_contracts import AIWorkerUpdate, AIWorkerUpdateStatus, FailedPayload
from app.application.ai_jobs import AIJobs
from app.application.ports.session_notification import PendingSessionNotification
from app.application.session_notification_contracts import BasePersistedNotification
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from tests.integration.api.test_authenticated_callback_lifecycle import _seed_session_and_job
from tests.support.fake_session_notifications import FakeSessionNotifications


@pytest.mark.anyio
@pytest.mark.parametrize("job_type", ["analyze_session", "analyze_answer", "generate_report"])
@pytest.mark.parametrize("conflict", [False, True])
async def test_failure_commit_and_notification_are_atomic(
    db_session_factory: async_sessionmaker[AsyncSession],
    job_type: str,
    conflict: bool,
) -> None:
    session_id, attempt_id, job_id, _ = await _seed_session_and_job(db_session_factory)
    async with db_session_factory() as db:
        job = await db.get(AnalysisJobModel, job_id)
        assert job is not None
        job.job_type = job_type
        session = await db.get(PracticeSessionModel, session_id)
        assert session is not None
        active_state = (
            SessionStatus.REPORT_GENERATING
            if job_type == "generate_report"
            else SessionStatus.QUESTIONS_IN_PROGRESS
            if job_type == "analyze_answer"
            else SessionStatus.ANALYZING
        )
        session.status = active_state
        await db.commit()

    class ObservingNotifications(FakeSessionNotifications):
        async def publish(
            self, notification: PendingSessionNotification
        ) -> BasePersistedNotification:
            async with db_session_factory() as observer:
                session = await observer.get(PracticeSessionModel, session_id)
                attempt = await observer.get(AnalysisAttemptModel, attempt_id)
                job = await observer.get(AnalysisJobModel, job_id)
                assert session is not None and session.status == SessionStatus.FAILED
                assert attempt is not None and attempt.status == AnalysisAttemptStatus.FAILED
                assert job is not None and job.status == AnalysisJobStatus.FAILED
                assert notification.payload == {
                    "state": "failed",
                    "version": session.version,
                    "current_attempt": 1,
                }
            return await super().publish(notification)

    notifications = ObservingNotifications()
    update = AIWorkerUpdate(
        schema_version=1,
        sequence=1,
        status=AIWorkerUpdateStatus.FAILED,
        occurred_at=datetime.now(UTC),
        trace_id="trc_auth_lifecycle_1",
        payload=FailedPayload(
            stage="speech",
            code="provider_timeout",
            retryable=True,
            attempts=3,
            message="Processing deadline exceeded.",
        ),
    )
    async with db_session_factory() as db:
        uow = SqlAlchemyUnitOfWork(db)
        service = AIJobs(uow, notifications=notifications)
        if conflict:
            with (
                patch.object(uow.sessions, "update", AsyncMock(side_effect=StaleEntityVersion)),
                pytest.raises(StaleEntityVersion),
            ):
                await service.record_update(job_id, update)
        else:
            await service.record_update(job_id, update)
            await service.record_update(job_id, update)

    async with db_session_factory() as db:
        session = await db.get(PracticeSessionModel, session_id)
        attempt = await db.get(AnalysisAttemptModel, attempt_id)
        job = await db.get(AnalysisJobModel, job_id)
        assert session is not None and attempt is not None and job is not None
        assert session.status == (active_state if conflict else SessionStatus.FAILED)
        assert attempt.status == (
            AnalysisAttemptStatus.RUNNING if conflict else AnalysisAttemptStatus.FAILED
        )
        assert job.status == (AnalysisJobStatus.QUEUED if conflict else AnalysisJobStatus.FAILED)
        assert job.last_update_sequence == (0 if conflict else 1)
    assert len((await notifications.replay(str(session_id), 0)).events) == (0 if conflict else 1)
