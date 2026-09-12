from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.rate_limit import rate_limit
from app.api.dependencies.services import (
    get_mail_sender,
    get_team_invitation_service,
    get_team_service,
)
from app.api.dependencies.settings import get_settings
from app.api.dependencies.team_authorization import get_team_member, get_team_owner
from app.api.schemas.mail import InvitationPageResponse, InviteMemberRequest, InviteMemberResponse
from app.api.schemas.team import (
    TeamMembershipPage,
    TeamMembershipResponse,
    TeamOwnerRequest,
    TeamPage,
    TeamRequest,
    TeamResponse,
)
from app.application.mail import MailSender
from app.application.services.team_invitation_service import (
    AlreadyConsumedInvitationError,
    AlreadyTeamMemberError,
    InvitationAlreadyExistsError,
    InvitationNotPendingError,
    InvitationPreconditionFailed,
    TeamInvitationNotFoundError,
    TeamInvitationService,
    invitation_etag,
)
from app.application.services.team_service import (
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
from app.settings import Settings

router = APIRouter(
    prefix="/api/v1",
)


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
    idempotency_key: str = Header(min_length=1, max_length=255),
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


@router.post(
    "/teams/{team_id}/invitations",
    response_model=InviteMemberResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["teams"],
    responses={
        201: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                }
            }
        }
    },
    dependencies=[
        Depends(
            rate_limit(
                name="create_invitation",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def invite_team_member(
    team_id: UUID,
    response: Response,
    background_tasks: BackgroundTasks,
    request: InviteMemberRequest,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamInvitationService = Depends(get_team_invitation_service),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    mail_sender: MailSender = Depends(get_mail_sender),
    settings: Settings = Depends(get_settings),
) -> InviteMemberResponse:
    try:
        invitation, token = await service.invite_member(
            team_id,
            request.email,
            request.role,
            idempotency_key=idempotency_key,
        )
    except AlreadyTeamMemberError as error:
        raise HTTPException(status_code=409, detail="already_team_member") from error
    except InvitationAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="invitation_already_exists") from error
    background_tasks.add_task(
        service.send_invitation_email,
        invitation,
        token,
        settings.frontend_url or "",
        mail_sender,
    )
    response.headers["ETag"] = f'"{invitation_etag(invitation)}"'
    return InviteMemberResponse.model_validate(invitation, from_attributes=True)


@router.get(
    "/teams/{team_id}/invitations",
    response_model=InvitationPageResponse,
    status_code=status.HTTP_200_OK,
    tags=["teams"],
)
async def list_team_invitations(
    team_id: UUID,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamInvitationService = Depends(get_team_invitation_service),
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> InvitationPageResponse:
    invitations, next_cursor = await service.list_invitations(team_id, cursor, limit)
    return InvitationPageResponse(
        items=[
            InviteMemberResponse.model_validate(invitation, from_attributes=True)
            for invitation in invitations
        ],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "/teams/{team_id}/invitations/{id}/resend",
    response_model=InviteMemberResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["teams"],
    responses={
        202: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                }
            }
        }
    },
    dependencies=[
        Depends(
            rate_limit(
                name="resend_invitation",
                limit=3,
                window_seconds=600,
            )
        )
    ],
)
async def resend_team_invitation(
    team_id: UUID,
    id: UUID,
    background_tasks: BackgroundTasks,
    response: Response,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamInvitationService = Depends(get_team_invitation_service),
    mail_sender: MailSender = Depends(get_mail_sender),
    settings: Settings = Depends(get_settings),
    resend_idempotency_key: str = Header(
        min_length=1,
        max_length=255,
        alias="Idempotency-Key",
    ),
) -> InviteMemberResponse:
    try:
        invitation, token = await service.resend_invitation(
            team_id,
            id,
            resend_idempotency_key=resend_idempotency_key,
        )
    except TeamInvitationNotFoundError as error:
        raise HTTPException(status_code=404, detail="team_invitation_not_found") from error
    except InvitationNotPendingError as error:
        raise HTTPException(status_code=409, detail="invitation_not_pending") from error
    background_tasks.add_task(
        service.send_invitation_email,
        invitation,
        token,
        settings.frontend_url or "",
        mail_sender,
    )
    response.headers["ETag"] = f'"{invitation_etag(invitation)}"'
    return InviteMemberResponse.model_validate(invitation, from_attributes=True)


@router.delete(
    "/teams/{team_id}/invitations/{id}", status_code=status.HTTP_204_NO_CONTENT, tags=["teams"]
)
async def revoke_team_invitation(
    team_id: UUID,
    id: UUID,
    _owner: TeamMember = Depends(get_team_owner),
    service: TeamInvitationService = Depends(get_team_invitation_service),
    if_match: str | None = Header(default="*", alias="If-Match"),
) -> Response:
    try:
        await service.revoke_invitation(team_id, id, if_match or "*")
    except TeamInvitationNotFoundError as error:
        raise HTTPException(status_code=404, detail="team_invitation_not_found") from error
    except AlreadyConsumedInvitationError as error:
        raise HTTPException(status_code=409, detail="already_consumed") from error
    except InvitationPreconditionFailed as error:
        raise HTTPException(status_code=412, detail="version precondition failed") from error
    except InvitationNotPendingError as error:
        raise HTTPException(status_code=409, detail="invitation_not_pending") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
