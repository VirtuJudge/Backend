from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_team_service
from app.api.dependencies.teamAuthorization import get_team_member, get_team_owner
from app.api.schemas.team import (
    TeamMembershipPage,
    TeamMembershipResponse,
    TeamOwnerRequest,
    TeamPage,
    TeamRequest,
    TeamResponse,
)
from app.application.services.teamService import (
    IdempotencyConflict,
    TeamMemberNotFound,
    TeamNameConflict,
    TeamOwnerCannotBeRemoved,
    TeamPreconditionFailed,
    TeamService,
    team_etag,
)
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.domain.user import User

router = APIRouter()


def team_response(team: Team) -> TeamResponse:
    return TeamResponse(id=team.id, name=team.name, created_at=team.created_at)


@router.get("/teams", response_model=TeamPage, tags=["teams"])
async def list_teams(
    current_user: User = Depends(get_current_user),
    service: TeamService = Depends(get_team_service),
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> TeamPage:
    teams, next_cursor = await service.list(current_user.id, cursor, limit)
    return TeamPage(
        items=[team_response(team) for team in teams],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["teams"],
)
async def create_team(
    request: TeamRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    service: TeamService = Depends(get_team_service),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=255),
) -> TeamResponse:
    try:
        team = await service.create(current_user.id, request.name, idempotency_key)
    except TeamNameConflict as error:
        raise HTTPException(status_code=409, detail="team_name_conflict") from error
    except IdempotencyConflict as error:
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from error
    response.headers["ETag"] = f'"{team_etag(team)}"'
    return team_response(team)


@router.get("/teams/{team_id}", response_model=TeamResponse, tags=["teams"])
async def get_team(
    team_id: UUID,
    response: Response,
    _membership: TeamMember = Depends(get_team_member),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    team = await service.get(team_id)
    response.headers["ETag"] = f'"{team_etag(team)}"'
    return team_response(team)


@router.patch("/teams/{team_id}", response_model=TeamResponse, tags=["teams"])
async def update_team(
    team_id: UUID,
    request: TeamRequest,
    response: Response,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamService = Depends(get_team_service),
    if_match: str | None = Header(default=None),
) -> TeamResponse:
    try:
        team = await service.update_name(team_id, request.name, if_match)
    except TeamPreconditionFailed as error:
        raise HTTPException(status_code=412, detail="team_precondition_failed") from error
    except TeamNameConflict as error:
        raise HTTPException(status_code=409, detail="team_name_conflict") from error
    response.headers["ETag"] = f'"{team_etag(team)}"'
    return team_response(team)


@router.patch(
    "/teams/{team_id}/owner",
    response_model=TeamMembershipResponse,
    tags=["teams"],
)
async def transfer_team_ownership(
    team_id: UUID,
    request: TeamOwnerRequest,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamService = Depends(get_team_service),
) -> TeamMembershipResponse:
    try:
        membership = await service.transfer_ownership(
            team_id,
            _owner.user_id,
            request.user_id,
        )
    except TeamMemberNotFound as error:
        raise HTTPException(status_code=404, detail="team_member_not_found") from error
    return TeamMembershipResponse.model_validate(membership, from_attributes=True)


@router.get("/teams/{team_id}/members", response_model=TeamMembershipPage, tags=["teams"])
async def list_team_members(
    team_id: UUID,
    _membership: TeamMember = Depends(get_team_member),
    service: TeamService = Depends(get_team_service),
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> TeamMembershipPage:
    members, next_cursor = await service.members(team_id, cursor, limit)
    return TeamMembershipPage(
        items=[
            TeamMembershipResponse.model_validate(member, from_attributes=True)
            for member in members
        ],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["teams"],
)
async def delete_team_member(
    team_id: UUID,
    user_id: UUID,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamService = Depends(get_team_service),
) -> Response:
    try:
        await service.remove_member(team_id, user_id)
    except TeamOwnerCannotBeRemoved as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot_remove_owner_until_ownership_is_transferred",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
