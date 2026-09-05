from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.api.schemas.team import (
	TeamMembershipPage,
	TeamMembershipResponse,
	TeamPage,
	TeamRequest,
	TeamResponse,
)
from app.application.services.teamService import (
	TeamForbidden,
	IdempotencyConflict,
	TeamNameConflict,
	TeamNotFound,
	TeamPreconditionFailed,
	TeamService,
	team_etag,
)
from app.domain.user import User
from app.infrastructure.database import get_session
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository

router = APIRouter()


def get_team_service(session: AsyncSession = Depends(get_session)) -> TeamService:
	return TeamService(SqlAlchemyTeamRepository(session))


def team_response(team) -> TeamResponse:
	return TeamResponse(id=team.id, name=team.name, created_at=team.created_at)


@router.get("/teams", response_model=TeamPage, tags=["teams"])
async def list_teams(
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
	cursor: UUID | None = Query(default=None),
	limit: int = Query(default=50, ge=1, le=100),
) -> TeamPage:
	teams, next_cursor = await service.list(current_user.id, cursor, limit)
	return TeamPage(items=[team_response(team) for team in teams], next_cursor=str(next_cursor) if next_cursor else None)


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED, tags=["teams"])
async def create_team(
	request: TeamRequest,
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
	idempotency_key: str | None = Header(default=None, min_length=1, max_length=255),
	response: Response = None,
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
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
	response: Response = None,
) -> TeamResponse:
	try:
		team = await service.get_authorized(team_id, current_user.id)
	except TeamNotFound as error:
		raise HTTPException(status_code=404, detail="team_not_found") from error
	except TeamForbidden as error:
		raise HTTPException(status_code=403, detail="team_forbidden") from error
	response.headers["ETag"] = f'"{team_etag(team)}"'
	return team_response(team)


@router.patch("/teams/{team_id}", response_model=TeamResponse, tags=["teams"])
async def update_team(
	team_id: UUID,
	request: TeamRequest,
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
	if_match: str | None = Header(default=None),
	response: Response = None,
) -> TeamResponse:
	try:
		team = await service.update_name(team_id, current_user.id, request.name, if_match)
	except TeamForbidden as error:
		raise HTTPException(status_code=403, detail="team_forbidden") from error
	except TeamPreconditionFailed as error:
		raise HTTPException(status_code=412, detail="team_precondition_failed") from error
	except TeamNameConflict as error:
		raise HTTPException(status_code=409, detail="team_name_conflict") from error
	response.headers["ETag"] = f'"{team_etag(team)}"'
	return team_response(team)


@router.get("/teams/{team_id}/members", response_model=TeamMembershipPage, tags=["teams"])
async def list_team_members(
	team_id: UUID,
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
	cursor: UUID | None = Query(default=None),
	limit: int = Query(default=50, ge=1, le=100),
) -> TeamMembershipPage:
	try:
		members, next_cursor = await service.members(team_id, current_user.id, cursor, limit)
	except TeamForbidden as error:
		raise HTTPException(status_code=403, detail="team_forbidden") from error
	except TeamNotFound as error:
		raise HTTPException(status_code=403, detail="team_forbidden") from error
	return TeamMembershipPage(
		items=[TeamMembershipResponse.model_validate(member, from_attributes=True) for member in members],
		next_cursor=str(next_cursor) if next_cursor else None,
	)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"])
async def delete_team_member(
	team_id: UUID,
	user_id: UUID,
	current_user: User = Depends(get_current_user),
	service: TeamService = Depends(get_team_service),
) -> Response:
	try:
		await service.remove_member(team_id, current_user.id, user_id)
	except (TeamForbidden, TeamNotFound) as error:
		raise HTTPException(status_code=403, detail="team_forbidden") from error
	return Response(status_code=status.HTTP_204_NO_CONTENT)


