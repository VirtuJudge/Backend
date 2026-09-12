import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.mail import MailDeliveryError, MailMessage, MailSender
from app.application.ports.invitation_resend_key_repository import InvitationResendKeyRepository
from app.application.ports.team_invitation_repository import TeamInvitationRepository
from app.application.ports.team_member_repository import TeamMemberRepository
from app.application.ports.team_repository import TeamRepository
from app.application.ports.user_repository import UserRepository
from app.application.templates.invitation import (
    INVITATION_SUBJECT,
    invitation_html_template,
    invitation_text_template,
)
from app.domain.team_invitation import DeliveryStatus, InvitationStatus, TeamInvitation
from app.domain.team_member import TeamMember


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


class InvitationNotPendingError(Exception):
    pass


def invitation_etag(invitation: TeamInvitation) -> str:
    return hashlib.sha256(f"{invitation.id}:{invitation.version}".encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


def invitation_message(recipient: str, url: str) -> MailMessage:
    return MailMessage(
        subject=INVITATION_SUBJECT,
        body=invitation_text_template(url),
        html_body=invitation_html_template(url),
        recipient=recipient,
    )


class TeamInvitationService:
    def __init__(
        self,
        repository: TeamInvitationRepository,
        team_repository: TeamRepository,
        member_repository: TeamMemberRepository,
        user_repository: UserRepository,
        resend_idomkey_repository: InvitationResendKeyRepository,
    ):
        self.repository = repository
        self.team_repository = team_repository
        self.member_repository = member_repository
        self.user_repository = user_repository
        self.resend_idomkey_repository = resend_idomkey_repository

    async def invite_member(
        self,
        team_id: UUID,
        email: str,
        role: str,
        idempotency_key: str,
    ) -> tuple[TeamInvitation, str]:
        email = normalize_email(email)
        existing = await self.repository.get_by_idempotency_key(idempotency_key)

        if existing is not None:
            return existing, ""

        if await self.member_repository.get_by_team_and_email(team_id, email):
            raise AlreadyTeamMemberError(f"{email} is already a member of this team")
        if await self.repository.exists_pending_invitation(team_id, email):
            raise InvitationAlreadyExistsError(f"A pending invitation already exists for {email}")

        token = secrets.token_urlsafe(32)  # send to email
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        invitation = TeamInvitation(
            id=uuid4(),
            team_id=team_id,
            email=email,
            role=role,
            status=InvitationStatus.PENDING,
            delivery_status=DeliveryStatus.QUEUED,
            idempotency_key=idempotency_key,
            token_hash=token_hash,
            delivery_attempts=0,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            resend_idempotency_keys=[],
            version=1,
        )
        invitation = await self.repository.create(invitation, idempotency_key)

        return invitation, token

    async def list_invitations(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 20
    ) -> tuple[list[TeamInvitation], UUID | None]:
        invitations = await self.repository.list_by_team(team_id, cursor=cursor, limit=limit)
        return invitations

    async def resend_invitation(
        self,
        team_id: UUID,
        invitation_id: UUID,
        resend_idempotency_key: str,
    ) -> tuple[TeamInvitation, str]:

        existing = await self.resend_idomkey_repository.get_by_resend_idempotency_key(
            resend_idempotency_key
        )
        existing_invitation = (
            await self.repository.get_by_id(existing.invitation_id) if existing else None
        )
        if existing_invitation is not None:
            return existing_invitation, ""

        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(
                f"Invitation with ID {invitation_id} not found for team {team_id}"
            )
        if invitation.status != InvitationStatus.PENDING:
            raise InvitationNotPendingError("Only pending invitations can be resent.")

        token = secrets.token_urlsafe(32)

        invitation.token_hash = hashlib.sha256(token.encode()).hexdigest()

        await self.resend_idomkey_repository.create(invitation, resend_idempotency_key)

        invitation.delivery_attempts += 1
        invitation.delivery_status = DeliveryStatus.QUEUED

        invitation = await self.repository.update(invitation)

        return invitation, token

    async def revoke_invitation(self, team_id: UUID, invitation_id: UUID, if_match: str) -> None:
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None or invitation.team_id != team_id:
            raise TeamInvitationNotFoundError(
                f"Invitation with ID {invitation_id} not found for team {team_id}"
            )
        if invitation.status == InvitationStatus.ACCEPTED:
            raise AlreadyConsumedInvitationError(
                "Cannot revoke an invitation that has already been accepted."
            )
        if invitation.status != InvitationStatus.PENDING:
            raise InvitationNotPendingError("Only pending invitations can be revoked.")

        normalized_if_match = if_match.strip('"') if if_match else ""
        if not normalized_if_match or (
            normalized_if_match != "*" and normalized_if_match != invitation_etag(invitation)
        ):
            raise InvitationPreconditionFailed(
                "ETag does not match. The invitation may have been modified by another process."
            )

        # Update the status to revoked
        invitation.status = InvitationStatus.REVOKED
        invitation = await self.repository.update(invitation)

    async def get_invitation_preview(self, token: str) -> tuple[str, str, TeamInvitation]:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result = await self.repository.get_invitation_by_token(token_hash)

        if result is None:
            raise TeamInvitationNotFoundError("Invitation not found")
        team_name, owner_name, invitation = result

        if invitation.status != InvitationStatus.PENDING:
            if invitation.status == InvitationStatus.REVOKED:
                raise TeamInvitationNotFoundError()

            if invitation.status == InvitationStatus.ACCEPTED:
                raise AlreadyConsumedInvitationError()

        if invitation.expires_at <= datetime.now(UTC):
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)

            raise TeamInvitationExpiredError("Invitation has expired")

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

        _team_name, _team_owner, invitation = result

        if invitation.status == InvitationStatus.REVOKED:
            raise TeamInvitationNotFoundError("Invitation not found")

        if invitation.status == InvitationStatus.ACCEPTED:
            raise AlreadyConsumedInvitationError("Invitation has already been accepted")

        if invitation.status == InvitationStatus.EXPIRED:
            raise TeamInvitationExpiredError("Invitation has expired")

        if invitation.status != InvitationStatus.PENDING:
            raise InvitationNotPendingError("Invitation cannot be accepted")

        now = datetime.now(UTC)

        if invitation.expires_at <= now:
            invitation.status = InvitationStatus.EXPIRED
            await self.repository.update(invitation)
            raise TeamInvitationExpiredError("Invitation has expired")

        user = await self.user_repository.get_by_id(user_id)

        if (
            user is None
            or user.email is None
            or normalize_email(user.email) != normalize_email(invitation.email)
        ):
            raise InvitationEmailMismatchError()

        membership = TeamMember(
            id=uuid4(),
            team_id=invitation.team_id,
            user_id=user_id,
            role=invitation.role,
            joined_at=now,
        )

        if not await self.repository.accept(invitation, membership):
            raise AlreadyConsumedInvitationError("Invitation has already been accepted")

        return membership

    async def send_invitation_email(
        self,
        invitation: TeamInvitation,
        token: str,
        frontend_url: str,
        mail_sender: MailSender,
    ) -> None:

        url = f"{frontend_url}/invitations/{token}"

        message = invitation_message(
            invitation.email,
            url,
        )

        try:
            await asyncio.to_thread(
                mail_sender.send,
                message,
            )

            invitation.delivery_status = DeliveryStatus.ACCEPTED

        except MailDeliveryError:
            invitation.delivery_status = DeliveryStatus.FAILED

        await self.repository.update(invitation)
