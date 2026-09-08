from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import Depends, Request

from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.application.services.projectService import ProjectService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.application.services.teamInvitationService import TeamInvitationService

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

def get_TeamInvitation_service(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamInvitationService:
    return cast(TeamInvitationService, request.app.state.team_invitation_service_factory(session))

def get_team_member_repository(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamMemberRepository:
    return cast(TeamMemberRepository, request.app.state.team_member_repository_factory(session))