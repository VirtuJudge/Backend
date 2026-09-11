from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import Depends, Request

from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.application.mail import MailSender
from app.application.services.assetStore import AssetStore
from app.application.services.consentPolicyService import ConsentPolicyService
from app.application.services.projectService import ProjectService
from app.application.services.teamInvitationService import TeamInvitationService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.application.interfaces.projectRepository import ProjectRepository
from app.application.interfaces.session_practice.analysis_attempt_repository import AnalysisAttemptRepository
from app.application.interfaces.session_practice.analysis_job_repository import AnalysisJobRepository
from app.application.interfaces.session_practice.session_consent_repository import SessionConsentRepository
from app.application.interfaces.session_practice.session_manifest_repository import SessionManifestRepository
from app.application.interfaces.session_practice.session_practice_repository import PracticeSessionRepository


async def get_session(request: Request) -> AsyncIterator[Any]:
    async for session in request.app.state.session_dependency(request):
        yield session


def get_user_service(
    request: Request,
    session: Any = Depends(get_session),
) -> UserService:
    return cast(UserService, request.app.state.user_service_factory(session))


def get_team_service(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamService:
    return cast(TeamService, request.app.state.team_service_factory(session))


def get_project_service(
    request: Request,
    session: Any = Depends(get_session),
) -> ProjectService:
    return cast(ProjectService, request.app.state.project_service_factory(session))


def get_team_invitation_service(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamInvitationService:
    return cast(TeamInvitationService, request.app.state.team_invitation_service_factory(session))


def get_team_member_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamMemberRepository:
    return cast(TeamMemberRepository, request.app.state.team_member_repository_factory(session))


def get_mail_sender(request: Request) -> MailSender:
    return cast(MailSender, request.app.state.mail_sender)


def get_asset_store(
    request: Request,
    session: Any = Depends(get_session),
) -> AssetStore:
    return cast(AssetStore, request.app.state.asset_store_factory(session))

def get_session_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> PracticeSessionRepository:
    return cast(PracticeSessionRepository, request.app.state.get_session_repository_factory(session))

def get_session_manifest_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> SessionManifestRepository:
    return cast(SessionManifestRepository, request.app.state.get_manifest_repository_factory(session))

def get_attempt_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> AnalysisAttemptRepository:
    return cast(AnalysisAttemptRepository, request.app.state.get_attempt_repository_factory(session))

def get_job_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> AnalysisJobRepository:
    return cast(AnalysisJobRepository, request.app.state.get_job_repository_factory(session))

def get_project_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> ProjectRepository:
    return cast(ProjectRepository, request.app.state.get_project_repository_factory(session))

def get_consent_policy_service(
    request: Request,
    session: Any = Depends(get_session),
) -> ConsentPolicyService:
    return cast(ConsentPolicyService, request.app.state.consent_policy_service_factory(session))