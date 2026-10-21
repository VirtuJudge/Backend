from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.qa import QARoundState
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    PracticeSessionModel,
    QARoundModel,
)
from tests.integration.api.test_authenticated_callback_lifecycle import _seed_session_and_job
from tests.integration.persistence.migrations.test_migrations import migration_configuration


@pytest.mark.anyio
async def test_migration_repairs_only_current_failed_active_sessions(tmp_path: Path) -> None:
    database = tmp_path / "failed-sessions.db"
    configuration = migration_configuration(f"sqlite:///{database}")
    command.upgrade(configuration, "3d4e5f6a7b8c")
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cases = []
    for state in (
        SessionStatus.ANALYZING,
        SessionStatus.REPORT_GENERATING,
        SessionStatus.CANCELLED,
        SessionStatus.COMPLETED,
    ):
        session_id, _, _, _ = await _seed_session_and_job(
            sessions,
            session_status=state,
            attempt_status=AnalysisAttemptStatus.FAILED,
        )
        cases.append(
            (
                session_id,
                state,
                state
                in {
                    SessionStatus.ANALYZING,
                    SessionStatus.REPORT_GENERATING,
                },
            )
        )
    session_id, attempt_id, _, _ = await _seed_session_and_job(
        sessions,
        attempt_status=AnalysisAttemptStatus.FAILED,
    )
    async with sessions() as db:
        attempt = await db.get(AnalysisAttemptModel, attempt_id)
        assert attempt is not None
        db.add(
            AnalysisAttemptModel(
                id=uuid4(),
                session_id=session_id,
                manifest_id=attempt.manifest_id,
                attempt_number=2,
                status=AnalysisAttemptStatus.RUNNING,
                version=1,
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()
    cases.append((session_id, SessionStatus.ANALYZING, False))

    command.upgrade(configuration, "head")
    async with sessions() as db:
        for session_id, previous, repaired in cases:
            session = await db.get(PracticeSessionModel, session_id)
            assert session is not None
            assert session.status == (SessionStatus.FAILED if repaired else previous)
            assert session.version == (2 if repaired else 1)
        assert await db.scalar(select(func.count()).select_from(AnalysisAttemptModel)) == 6
        attempt_ids = list(
            (
                await db.scalars(
                    select(AnalysisAttemptModel.id).where(
                        AnalysisAttemptModel.session_id == session_id,
                    )
                )
            ).all()
        )
        assert len(attempt_ids) == 2
        for round_attempt_id in attempt_ids:
            db.add(
                QARoundModel(
                    id=uuid4(),
                    practice_session_id=session_id,
                    analysis_attempt_id=round_attempt_id,
                    state=QARoundState.IN_PROGRESS,
                    current_question_id=None,
                    follow_up_count=0,
                    version=1,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        await db.commit()
    with pytest.raises(RuntimeError, match="preserve history"):
        command.downgrade(configuration, "3d4e5f6a7b8c")
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(QARoundModel)) == 2
    await engine.dispose()
