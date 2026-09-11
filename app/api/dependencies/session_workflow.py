from fastapi import Depends

from app.application.services.consentPolicyService import ConsentPolicyService
from .services import (get_attempt_repository, get_consent_policy_service 
                    ,get_session_manifest_repository
                    , get_session_repository
                    ,get_job_repository
                    ,get_project_repository)

from app.application.session_workflow import SessionWorkflow
from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.session_practice.analysis_attempt_repository import AnalysisAttemptRepository
from app.application.interfaces.session_practice.analysis_job_repository import AnalysisJobRepository
from app.application.interfaces.session_practice.session_consent_repository import SessionConsentRepository
from app.application.interfaces.session_practice.session_manifest_repository import SessionManifestRepository
from app.application.interfaces.session_practice.session_practice_repository import PracticeSessionRepository

def get_session_workflow(
    session_repository: PracticeSessionRepository = Depends(
        get_session_repository
    ),
    manifest_repository: SessionManifestRepository = Depends(
        get_session_manifest_repository
    ),
    attempt_repository: AnalysisAttemptRepository = Depends(
        get_attempt_repository
    ),
    job_repository: AnalysisJobRepository = Depends(
        get_job_repository
    ),
    project_repository: ProjectRepository = Depends(
        get_project_repository
    ),
    consent_policy: ConsentPolicyService = Depends(
        get_consent_policy_service
    )
) -> SessionWorkflow:
    return SessionWorkflow(
        session_repository=session_repository,
        manifest_repository=manifest_repository,
        attempt_repository=attempt_repository,
        job_repository=job_repository,
        consent_policy=consent_policy,
        project_repository=project_repository,
    )