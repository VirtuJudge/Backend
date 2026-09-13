import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow import SqlAlchemyUnitOfWork

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.anyio
async def test_simultaneous_analysis_start_is_idempotent_in_postgres() -> None:
    database_url = os.environ.get("ASSET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("ASSET_TEST_DATABASE_URL not configured")

    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(configuration, "head")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    asset_version_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    idempotency_key = f"start-{uuid4()}"

    try:
        async with session_factory() as setup_session:
            setup_session.add_all(
                [
                    UserModel(
                        id=user_id,
                        email=f"workflow-race-{uuid4().hex[:8]}@example.com",
                        issuer="https://auth.example",
                        subject=f"workflow-race-{uuid4()}",
                        created_at=now,
                    ),
                    TeamModel(id=team_id, name=f"Race team {uuid4()}", created_at=now),
                ]
            )
            await setup_session.flush()
            setup_session.add_all(
                [
                    TeamMemberModel(
                        id=uuid4(),
                        team_id=team_id,
                        user_id=user_id,
                        role="owner",
                        joined_at=now,
                    ),
                    ProjectModel(
                        id=project_id,
                        team_id=team_id,
                        name="Analysis start race",
                        description=None,
                        created_at=now,
                    ),
                ]
            )
            await setup_session.flush()
            setup_session.add(
                AssetModel(
                    id=asset_id,
                    project_id=project_id,
                    kind="presentation_video",
                    state="verified",
                    file_name="presentation.mp4",
                    current_version_id=asset_version_id,
                    created_by=user_id,
                    created_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                AssetVersionModel(
                    id=asset_version_id,
                    asset_id=asset_id,
                    version_number=1,
                    state="verified",
                    storage_key=f"tests/{asset_version_id}",
                    file_name="presentation.mp4",
                    declared_media_type="video/mp4",
                    declared_size_bytes=1,
                    media_type="video/mp4",
                    size_bytes=1,
                    checksum=f"sha256:{'0' * 64}",
                    created_by=user_id,
                    created_at=now,
                    completed_at=now,
                    upload_expires_at=now,
                )
            )
            await setup_session.flush()
            setup_session.add(
                PracticeSessionModel(
                    id=session_id,
                    name="Concurrent analysis start",
                    project_id=project_id,
                    created_by=user_id,
                    status=SessionStatus.READY,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    consent_granted=False,
                )
            )
            await setup_session.flush()
            setup_session.add(
                SessionManifestModel(
                    id=manifest_id,
                    session_id=session_id,
                    presentation_version_id=asset_version_id,
                    rubric_id="startup_pitch",
                    rubric_version=1,
                )
            )
            await setup_session.commit()

        async def start_analysis() -> AnalysisAttempt:
            async with session_factory() as database_session:
                workflow = SessionWorkflow(SqlAlchemyUnitOfWork(database_session))
                return await workflow.create_analysis_attempt(
                    session_id=session_id,
                    actor_id=user_id,
                    idempotency_key=idempotency_key,
                    consent_accepted=True,
                    consent_policy_version=1,
                )

        first, second = await asyncio.gather(start_analysis(), start_analysis())

        assert first.id == second.id
        async with session_factory() as verification_session:
            attempt_count = await verification_session.scalar(
                select(func.count())
                .select_from(AnalysisAttemptModel)
                .where(AnalysisAttemptModel.session_id == session_id)
            )
            job_count = await verification_session.scalar(
                select(func.count())
                .select_from(AnalysisJobModel)
                .where(AnalysisJobModel.practice_session_id == session_id)
            )
        assert attempt_count == 1
        assert job_count == 1
    finally:
        await engine.dispose()
