
import hashlib
import secrets

from alembic.environment import Optional

from app.application.interfaces.teamInvitationRepository import TeamInvitationRepository
from app.application.interfaces.teamRepository import TeamRepository
from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.application.interfaces.userRepository import UserRepository
from app.domain.team_member import TeamMember
from app.domain.team_invitation import TeamInvitation, InvitationStatus
from uuid import UUID, uuid4
from datetime import UTC, datetime, timedelta

class InvitationAlreadyExistsError(Exception):
    pass


class AlreadyTeamMemberError(Exception):
    pass

class TeamInvitationNotFoundError(Exception):
    pass

class AlreadyConsumedInvitationError(Exception):
    pass

class TeamInvitationExpiredError(Exception):
    pass

class InvitationEmailMismatchError(Exception):
    pass

class TeamInvitationService:
    def __init__(self, repository: TeamInvitationRepository , team_repository: TeamRepository, member_repository: TeamMemberRepository, user_repository: UserRepository):
        self.repository = repository
        self.team_repository = team_repository
        self.member_repository = member_repository
        self.user_repository = user_repository

    async def invite_member(self, team_id: UUID, email: str, role: str, idempotency_key: str) -> TeamInvitation:
            if not await self.member_repository.get_by_team_and_email(team_id, email):
                raise AlreadyTeamMemberError(f"{email} is already a member of this team")
            if await self.repository.exists_pending_invitation(team_id, email):
                raise InvitationAlreadyExistsError(f"A pending invitation already exists for {email}")

            existing = await self.repository.get_by_idempotency_key(
            idempotency_key
            )

            if existing is not None:
                return existing

            token = secrets.token_urlsafe(32) #send to email
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            
            invitation = TeamInvitation(
                id=uuid4(),
                team_id=team_id,
                email=email,
                role=role,
                status=TeamInvitation.status.PENDING,
                delivery_status=TeamInvitation.delivery_status.QUEUED,
                idempotency_key=idempotency_key,
                token_hash=token_hash,
                delivery_attempts=0,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            # Logic to resend the invitation (e.g., send an email) goes here
            # For example, you might call an email service to send the invitation
    
            invitation = await self.repository.create(invitation)
            return invitation



    async def list_invitations(self, team_id: UUID, cursor: UUID | None = None, limit: int = 20):
        invitations = await self.repository.list_by_team(team_id, cursor=cursor, limit=limit)
        return invitations

    async def resend_invitation(self, team_id: UUID, invitation_id: UUID):
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(f"Invitation with ID {invitation_id} not found for team {team_id}")
        if invitation.status != TeamInvitation.status.PENDING:
            raise ValueError("Only pending invitations can be resent.")

        # Logic to resend the invitation (e.g., send an email) goes here
        # For example, you might call an email service to send the invitation again

        # Update the delivery status and attempts
        invitation.delivery_status = TeamInvitation.delivery_status.QUEUED
        invitation.delivery_attempts += 1
        await self.repository.update(invitation)

    async def revoke_invitation(self, team_id: UUID, invitation_id: UUID):
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(f"Invitation with ID {invitation_id} not found for team {team_id}")
        if invitation.status == TeamInvitation.status.ACCEPTED:
            raise AlreadyConsumedInvitationError("Cannot revoke an invitation that has already been accepted.")
        if invitation.status != TeamInvitation.status.PENDING:
            raise ValueError("Only pending invitations can be revoked.")

        # Update the status to revoked
        invitation.status = TeamInvitation.status.REVOKED
        await self.repository.update(invitation)

    async def get_invitation_preview(
        self,
        token: str,
    ):
        result = await self.repository.get_invitation_by_token(token)

        if result is None:
            raise TeamInvitationNotFoundError(
                f"Invitation with token {token} not found"
            )

        team_name, owner_name, invitation = result

        if invitation.expires_at <= datetime.utcnow():
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)

            raise TeamInvitationExpiredError(
                f"Invitation with token {token} has expired"
            )

        return team_name, owner_name, invitation

    async def accept_invitation(
        self,
        token: str,
        user_id: UUID,
    ) -> TeamMember:
        result = await self.repository.get_invitation_by_token(token)

        if result is None:
            raise TeamInvitationNotFoundError(
                f"Invitation with token {token} not found"
            )

        team_name, owner_name, invitation = result

        if invitation.expires_at <= datetime.utcnow():
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)

            raise TeamInvitationExpiredError(
                f"Invitation with token {token} has expired"
            )

        # Make sure the authenticated user matches the invitation
        user = await self.user_repository.get_by_id(user_id)

        if user is None or user.email != invitation.email:
            raise InvitationEmailMismatchError()
        if invitation.expires_at <= datetime.utcnow():
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)

            raise TeamInvitationExpiredError(
                f"Invitation with token {token} has expired"
            )

        membership = TeamMember(
            id=uuid4(),
            team_id=invitation.team_id,
            user_id=user_id,
            role=invitation.role,
            joined_at=datetime.utcnow()
        )

        await self.member_repository.create(membership)

        invitation.status = InvitationStatus.ACCEPTED
        await self.repository.update(invitation)

        return membership
        
