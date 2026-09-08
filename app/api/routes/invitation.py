from re import I
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_TeamInvitation_service
from app.api.schemas.mail import InvitationPreview
from app.api.schemas.team import TeamMembershipResponse
from app.application.services.teamInvitationService import AlreadyConsumedInvitationError, InvitationEmailMismatchError, TeamInvitationExpiredError, TeamInvitationNotFoundError, TeamInvitationService
from app.domain.user import User

router = APIRouter()

@router.get("/invitations/{token}",status_code=status.HTTP_200_OK, response_model=InvitationPreview, tags=["invitations"])
async def invitation_preview(
    token: str,
    service: TeamInvitationService = Depends(get_TeamInvitation_service),
) -> InvitationPreview:
    try:
        team_name, owner_name, invitation = await service.get_invitation_preview(token)
    except TeamInvitationNotFoundError as error:
        raise HTTPException(status_code=404, detail="Invitation not found")
    except TeamInvitationExpiredError as error:
        raise HTTPException(status_code=410, detail="Invitation has expired")
    except AlreadyConsumedInvitationError as error:
        raise HTTPException(status_code=409, detail="Invitation has already been accepted")
    return InvitationPreview(
        team_name=team_name,
        role=invitation.role,
        invited_email=invitation.email,
        invited_by_name=owner_name,
        expires_at=invitation.expires_at
    )

@router.post(
    "api/v1/invitations/{token}/accept",
    response_model=TeamMembershipResponse,
    status_code=status.HTTP_200_OK,
    tags=["invitations"],
)
async def accept_invitation(
    token: str,
    current_user: User = Depends(get_current_user),
    service: TeamInvitationService = Depends(get_TeamInvitation_service),
) -> TeamMembershipResponse:
    try:
        membership = await service.accept_invitation(token, current_user.id)
    except TeamInvitationExpiredError:
        raise HTTPException(status_code=410, detail="Invitation has expired")
    except InvitationEmailMismatchError:
        raise HTTPException(status_code=409, detail="Invitation email does not match the authenticated user")
    return TeamMembershipResponse.model_validate(membership, from_attributes=True)
