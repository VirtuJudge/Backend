from types import TracebackType
from typing import Protocol

from app.application.ports import ProjectRepository
from app.application.ports.session_practice.analysis_attempt_repository import (
    AnalysisAttemptRepository,
)
from app.application.ports.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.application.ports.session_practice.qa_repository import QARepository
from app.application.ports.session_practice.report_repository import ReportRepository
from app.application.ports.session_practice.session_command_idempotency_repository import (
    SessionCommandIdempotencyRepository,
)
from app.application.ports.session_practice.session_manifest_repository import (
    SessionManifestRepository,
)
from app.application.ports.session_practice.session_practice_repository import (
    PracticeSessionRepository,
)
from app.application.ports.session_practice.speaker_mapping_repository import (
    SpeakerMappingRepository,
)


class UnitOfWork(Protocol):
    sessions: PracticeSessionRepository
    manifests: SessionManifestRepository
    attempts: AnalysisAttemptRepository
    projects: ProjectRepository
    jobs: AnalysisJobRepository
    speaker_mappings: SpeakerMappingRepository
    idempotency: SessionCommandIdempotencyRepository
    qa: QARepository
    reports: ReportRepository

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
