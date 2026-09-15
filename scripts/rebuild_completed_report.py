"""Rebuild one completed report from its checksum-verified R2 evaluation artifact."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.application.ai_jobs import AIJobs  # noqa: E402
from app.infrastructure.database import create_database_engine  # noqa: E402
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (  # noqa: E402
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.storage.s3_object_storage import S3ObjectStorage  # noqa: E402
from app.settings import Settings  # noqa: E402


async def rebuild(settings: Settings, session_id: UUID) -> UUID:
    engine = create_database_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            report = await AIJobs(
                SqlAlchemyUnitOfWork(session), storage=S3ObjectStorage(settings)
            ).rebuild_completed_report(session_id)
            return report.id
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", type=UUID, help="Completed practice session to rebuild")
    parser.add_argument("--env-file", type=Path, help="Environment file for database and R2 access")
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    report_id = asyncio.run(rebuild(settings, args.session_id))
    print(f"Rebuilt report {report_id} for session {args.session_id}.")


if __name__ == "__main__":
    main()
