from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.infrastructure.persistence.configurations.session_workflow import AnalysisJobModel
from tests.integration.persistence.migrations.test_migrations import migration_configuration
from tests.integration.persistence.test_report_repository import _seed_session_ancestry


@pytest.mark.anyio
@pytest.mark.parametrize("duplicates", [False, True])
async def test_report_job_migration_preserves_history_and_prevents_new_duplicates(
    tmp_path: Path,
    duplicates: bool,
) -> None:
    path = tmp_path / "report-uniqueness.db"
    configuration = migration_configuration(f"sqlite:///{path}")
    command.upgrade(configuration, "4e5f6a7b8c9d")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            session, attempt, _ = await _seed_session_ancestry(db)
            values = dict(
                practice_session_id=session.id,
                attempt_id=attempt.id,
                analysis_attempt=1,
                job_type="generate_report",
                status=AnalysisJobStatus.COMPLETED,
                correlation_id=uuid4(),
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
            db.add(AnalysisJobModel(id=uuid4(), **values))
            if duplicates:
                db.add(AnalysisJobModel(id=uuid4(), **values))
            await db.commit()
        if duplicates:
            with pytest.raises(RuntimeError, match="preserve history"):
                command.upgrade(configuration, "head")
        else:
            command.upgrade(configuration, "head")
            async with sessions() as db:
                db.add(AnalysisJobModel(id=uuid4(), **values))
                with pytest.raises(IntegrityError):
                    await db.commit()
            command.downgrade(configuration, "4e5f6a7b8c9d")
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(AnalysisJobModel)) == (
                2 if duplicates else 1
            )
        sync = create_engine(f"sqlite:///{path}")
        with sync.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT version_num FROM backend_alembic_version"
                ).scalar_one()
                == "4e5f6a7b8c9d"
            )
        sync.dispose()
    finally:
        await engine.dispose()
