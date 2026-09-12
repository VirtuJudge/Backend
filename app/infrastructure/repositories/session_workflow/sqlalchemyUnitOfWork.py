from types import TracebackType

from app.infrastructure.repositories.sqlalchemyProjectRepository import SqlAlchemyProjectRepository
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.session_practice.unit_of_work_repository import UnitOfWork
from app.infrastructure.repositories.session_workflow.sqlalcemyAnalysisAttemptRepository import (
    SqlAlchemyAnalysisAttemptRepository,
)
from app.infrastructure.repositories.session_workflow.sqlalchemyAnalysisJobRepository import (
    SqlAlchemyAnalysisJobRepository,
)
from app.infrastructure.repositories.session_workflow.sqlalchemyPracticeSessionRepository import (
    SqlAlchemyPracticeSessionRepository,
)
from app.infrastructure.repositories.session_workflow.sqlalchemySessionManifestRepository import (
    SqlAlchemySessionManifestRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

        self.practice_sessions = SqlAlchemyPracticeSessionRepository(session)
        self.manifests = SqlAlchemySessionManifestRepository(session)
        self.attempts = SqlAlchemyAnalysisAttemptRepository(session)
        self.projects = SqlAlchemyProjectRepository(session)
        self.attempts = SqlAlchemyAnalysisAttemptRepository(session)
        self.jobs = SqlAlchemyAnalysisJobRepository(session)

    async def commit(self) -> None:
        await self.session.commit()

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
