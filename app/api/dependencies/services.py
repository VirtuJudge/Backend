from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, Request

from app.application.services.projectService import ProjectService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService


async def get_session(request: Request) -> AsyncIterator[Any]:
    async for session in request.app.state.session_dependency(request):
        yield session


def get_user_service(
    request: Request,
    session: Any = Depends(get_session),
) -> UserService:
    return request.app.state.user_service_factory(session)


def get_team_service(
    request: Request,
    session: Any = Depends(get_session),
) -> TeamService:
    return request.app.state.team_service_factory(session)


def get_project_service(
    request: Request,
    session: Any = Depends(get_session),
) -> ProjectService:
    return request.app.state.project_service_factory(session)
