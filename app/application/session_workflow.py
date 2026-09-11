from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.session_practice.session_practice_repository import (
    PracticeSessionRepository,
)
from app.application.interfaces.session_practice.session_manifest_repository import (
    SessionManifestRepository,
)
from app.application.interfaces.session_practice.session_consent_repository import (
    SessionConsentRepository,
)
from app.application.interfaces.session_practice.analysis_attempt_repository import (
    AnalysisAttemptRepository,
)
from app.application.interfaces.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.application.services.consentPolicyService import ConsentPolicyService
class SessionWorkflow:
    def __init__(
        self,
        session_repository: PracticeSessionRepository,
        manifest_repository: SessionManifestRepository,
        consent_repository: SessionConsentRepository,
        attempt_repository: AnalysisAttemptRepository,
        job_repository: AnalysisJobRepository,
        consent_policy: ConsentPolicyService,
        project_repository: ProjectRepository
    ) -> None:
        self._sessions = session_repository
        self._manifests = manifest_repository
        self._consents = consent_repository
        self._attempts = attempt_repository
        self._jobs = job_repository
        self._project_repository = project_repository

        self._consent_policy = consent_policy

    async def create_session():
        ...

    async def start_analysis():
        ...

    async def submit_answer():
        ...

    async def skip_answer():
        ...

    async def retry():
        ...

    async def cancel():
        ...