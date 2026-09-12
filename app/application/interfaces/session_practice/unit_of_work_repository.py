from typing import Protocol

from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.session_practice.analysis_attempt_repository import (
    AnalysisAttemptRepository,
)
from app.application.interfaces.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.application.interfaces.session_practice.session_manifest_repository import (
    SessionManifestRepository,
)
from app.application.interfaces.session_practice.session_practice_repository import (
    PracticeSessionRepository,
)


class UnitOfWork(Protocol):
    sessions: PracticeSessionRepository
    manifests: SessionManifestRepository
    attempts: AnalysisAttemptRepository
    projects: ProjectRepository
    jobs: AnalysisJobRepository

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type, exc, tb): ...
