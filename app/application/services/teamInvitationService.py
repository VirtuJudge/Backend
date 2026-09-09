
import hashlib
import secrets


from app.application.interfaces.teamInvitationRepository import TeamInvitationRepository
from app.application.interfaces.teamRepository import TeamRepository
from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.application.interfaces.invitationResendKeyRepository import InvitationResendKeyRepository
from app.application.interfaces.userRepository import UserRepository
from app.application.mail import MailDeliveryError, MailMessage, MailSender
from app.domain.team_member import TeamMember
from app.domain.team_invitation import DeliveryStatus, TeamInvitation, InvitationStatus
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

class InvitationPreconditionFailed(Exception):
    pass

def invitation_etag(invitation: TeamInvitation) -> str:
    return hashlib.sha256(f"{invitation.id}:{invitation.version}".encode()).hexdigest()
class TeamInvitationService:
    def __init__(self, repository: TeamInvitationRepository 
                , team_repository: TeamRepository
                , member_repository: TeamMemberRepository
                , user_repository: UserRepository
                , resend_idomkey_repository: InvitationResendKeyRepository):
        self.repository = repository
        self.team_repository = team_repository
        self.member_repository = member_repository
        self.user_repository = user_repository
        self.resend_idomkey_repository = resend_idomkey_repository

    async def invite_member(self, team_id: UUID, email: str
                            , role: str
                            , idempotency_key: str
                            , frontend_url: str
                            , mail_sender: MailSender) -> TeamInvitation:
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
            invitation = await self.repository.create(invitation)

            url = f"{frontend_url}/invitations/{token}"

            message = MailMessage(
                subject="You're invited to join a team!",
                body=(
                    "Hello,\n\n"
                    "You have been invited to join the team. "
                    f"Please use the following link to accept the invitation: {url}\n\n"
                    "Best regards,\nTeam"
                ),
                recipient=email,
            )
            try:
                mail_sender.send(message)

                invitation.delivery_status = (
                    TeamInvitation.delivery_status.ACCEPTED
                )

            except MailDeliveryError:
                invitation.delivery_status = (
                    TeamInvitation.delivery_status.FAILED
                )

            await self.repository.update(invitation)

            return invitation



    async def list_invitations(self, team_id: UUID, cursor: UUID | None = None, limit: int = 20):
        invitations = await self.repository.list_by_team(team_id, cursor=cursor, limit=limit)
        return invitations

    async def resend_invitation(self, team_id: UUID
                                , invitation_id: UUID
                                , frontend_url: str
                                , mail_sender: MailSender
                                ,resend_idempotency_key: str) -> TeamInvitation:

        existing = await self.resend_idomkey_repository.get_by_resend_idempotency_key(
                    resend_idempotency_key
                )
        existing_invitation = await self.repository.get_by_id(existing.invitation_id) if existing else None
        if existing_invitation is not None:
            return existing_invitation
        
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(f"Invitation with ID {invitation_id} not found for team {team_id}")
        if invitation.status != TeamInvitation.status.PENDING:
            raise ValueError("Only pending invitations can be resent.")

        token = secrets.token_urlsafe(32)

        invitation.token_hash = hashlib.sha256(
            token.encode()
        ).hexdigest()

        await self.resend_idomkey_repository.create(invitation, resend_idempotency_key)

        invitation.delivery_attempts += 1
        invitation.delivery_status = DeliveryStatus.QUEUED

        url = f"{frontend_url}/invitations/{token}"
        
        message = MailMessage(
            subject="You're invited to join a team!",
            body=(
                "Hello,\n\n"
                "You have been invited to join the team. "
                f"Please use the following link to accept the invitation: {url}\n\n"
                "Best regards,\nTeam"
            ),
            recipient=invitation.email,
        )
        try:
            mail_sender.send(message)

            invitation.delivery_status = (
                TeamInvitation.delivery_status.ACCEPTED
            )

        except MailDeliveryError:
            invitation.delivery_status = (
                TeamInvitation.delivery_status.FAILED
            )

        await self.repository.update(invitation)

        return invitation

    async def revoke_invitation(self, team_id: UUID
                                , invitation_id: UUID, if_match: str):
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(f"Invitation with ID {invitation_id} not found for team {team_id}")
        if invitation.status == TeamInvitation.status.ACCEPTED:
            raise AlreadyConsumedInvitationError("Cannot revoke an invitation that has already been accepted.")
        if invitation.status != TeamInvitation.status.PENDING:
            raise ValueError("Only pending invitations can be revoked.")

        if if_match is None or if_match.strip('"') != invitation_etag(invitation):
            raise InvitationPreconditionFailed("ETag does not match. The invitation may have been modified by another process.")
        
        # Update the status to revoked
        invitation.status = TeamInvitation.status.REVOKED
        await self.repository.update(invitation)

    async def get_invitation_preview(
        self,
        token: str,
    ):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result = await self.repository.get_invitation_by_token(token_hash)

        if result is None:
            raise TeamInvitationNotFoundError(
                f"Invitation with token {token} not found"
            )
        team_name, owner_name, invitation = result

        if invitation.status != InvitationStatus.PENDING:
            if invitation.status == InvitationStatus.REVOKED:
                raise TeamInvitationNotFoundError()

            if invitation.status == InvitationStatus.ACCEPTED:
                raise AlreadyConsumedInvitationError()
        

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
        
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        result = await self.repository.get_invitation_by_token(token_hash)

        if result is None:
            raise TeamInvitationNotFoundError("Invitation not found")

        team_name, team_owner, invitation = result

        
        if invitation.status == InvitationStatus.REVOKED:
            raise TeamInvitationNotFoundError("Invitation not found")

        if invitation.status == InvitationStatus.ACCEPTED:
            raise AlreadyConsumedInvitationError(
                "Invitation has already been accepted"
            )

        if invitation.status == InvitationStatus.EXPIRED:
            raise TeamInvitationExpiredError(
                "Invitation has expired"
            )

        if invitation.status != InvitationStatus.PENDING:
            raise ValueError("Invitation cannot be accepted")

        now = datetime.now(UTC)

        if invitation.expires_at <= now:
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)

            raise TeamInvitationExpiredError(
                "Invitation has expired"
            )

        user = await self.user_repository.get_by_id(user_id)

        if user is None or user.email != invitation.email:
            raise InvitationEmailMismatchError()

        membership = TeamMember(
            id=uuid4(),
            team_id=invitation.team_id,
            user_id=user_id,
            role=invitation.role,
            joined_at=now,
        )

        await self.member_repository.create(membership)

        invitation.status = InvitationStatus.ACCEPTED

        await self.repository.update(invitation)

        return membership
