from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.repositories.session_workflow.sqlalchemyAnalysisJobRepository import SqlAlchemyAnalysisJobRepository
from app.infrastructure.repositories.sqlalcemyAnalysisAttemptRepository import SqlAlchemyAnalysisAttemptRepository
from app.infrastructure.repositories.session_workflow.sqlalchemyPracticeSessionRepository import SqlAlchemyPracticeSessionRepository
from app.infrastructure.repositories.session_workflow.sqlalchemySessionConsentRepository import SqlAlchemySessionConsentRepository
from app.infrastructure.repositories.session_workflow.sqlalchemySessionManifestRepository import SqlAlchemySessionManifestRepository

class SqlAlchemyUnitOfWork:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

        self.practice_sessions = (
            SqlAlchemyPracticeSessionRepository(session)
        )

        self.manifests = (
            SqlAlchemySessionManifestRepository(session)
        )

        self.consents = (
            SqlAlchemySessionConsentRepository(session)
        )

        self.attempts = (
            SqlAlchemyAnalysisAttemptRepository(session)
        )

        self.jobs = (
            SqlAlchemyAnalysisJobRepository(session)
        )


    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()