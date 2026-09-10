from uuid import UUID

from fastapi import Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_team_service
from app.application.services.team_service import TeamNotFound, TeamService
from app.domain.team_member import TeamMember
from app.domain.user import User


async def get_team_member(
    team_id: UUID,
    current_user: User = Depends(get_current_user),
    service: TeamService = Depends(get_team_service),
) -> TeamMember:
    try:
        await service.get(team_id)
    except TeamNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="team_not_found",
        ) from error
    membership = await service.get_membership(team_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team_forbidden")
    return membership


async def get_team_owner(
    membership: TeamMember = Depends(get_team_member),
) -> TeamMember:
    if membership.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team_forbidden")
    return membership
