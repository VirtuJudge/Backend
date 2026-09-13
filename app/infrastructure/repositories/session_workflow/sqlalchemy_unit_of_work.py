from types import TracebackType

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.repositories import SqlAlchemyProjectRepository

from .sqlalchemy_analysis_attempt_repository import (
    SqlAlchemyAnalysisAttemptRepository,
)
from .sqlalchemy_analysis_job_repository import (
    SqlAlchemyAnalysisJobRepository,
)
from .sqlalchemy_practice_session_repository import (
    SqlAlchemyPracticeSessionRepository,
)
from .sqlalchemy_session_command_idempotency_repository import (
    SqlAlchemySessionCommandIdempotencyRepository,
)
from .sqlalchemy_session_manifest_repository import (
    SqlAlchemySessionManifestRepository,
)
from .sqlalchemy_speaker_mapping_repository import (
    SqlAlchemySpeakerMappingRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

        self.sessions = SqlAlchemyPracticeSessionRepository(session)
        self.manifests = SqlAlchemySessionManifestRepository(session)
        self.attempts = SqlAlchemyAnalysisAttemptRepository(session)
        self.projects = SqlAlchemyProjectRepository(session)
        self.jobs = SqlAlchemyAnalysisJobRepository(session)
        self.speaker_mappings = SqlAlchemySpeakerMappingRepository(session)
        self.idempotency = SqlAlchemySessionCommandIdempotencyRepository(session)

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise IdempotencyConflict("Database integrity conflict.") from exc

    async def rollback(self) -> None:
        await self.session.rollback()

    async def __aenter__(self) -> "UnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
