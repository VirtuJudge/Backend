import hashlib
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.application.interfaces.invitationResendKeyRepository import InvitationResendKeyRepository
from app.application.interfaces.teamInvitationRepository import TeamInvitationRepository
from app.application.interfaces.teamMemberRepository import TeamMemberRepository
from app.application.interfaces.teamRepository import TeamRepository
from app.application.interfaces.userRepository import UserRepository
from app.application.services.teamInvitationService import (
    AlreadyConsumedInvitationError,
    TeamInvitationExpiredError,
    TeamInvitationService,
)
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import DeliveryStatus, InvitationStatus, TeamInvitation
from app.domain.team_member import TeamMember
from app.domain.user import User
from app.infrastructure.mail import FakeMailSender


class MemoryInvitationRepository:
    def __init__(self, invitation: TeamInvitation | None = None) -> None:
        self.invitation = invitation
        self.accepted_memberships: list[TeamMember] = []

    async def create(self, invitation: TeamInvitation, idempotency_key: str) -> TeamInvitation:
        self.invitation = invitation
        return invitation

    async def exists_pending_invitation(self, team_id: UUID, email: str) -> bool:
        return False

    async def list_by_team(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 20
    ) -> tuple[list[TeamInvitation], UUID | None]:
        return ([self.invitation] if self.invitation is not None else []), None

    async def get_by_idempotency_key(self, idempotency_key: str) -> TeamInvitation | None:
        if self.invitation is not None and self.invitation.idempotency_key == idempotency_key:
            return self.invitation
        return None

    async def get_by_id(self, invitation_id: UUID) -> TeamInvitation | None:
        if self.invitation is not None and self.invitation.id == invitation_id:
            return self.invitation
        return None

    async def update(self, invitation: TeamInvitation) -> TeamInvitation:
        invitation.version += 1
        self.invitation = invitation
        return invitation

    async def get_invitation_by_token(self, token: str) -> tuple[str, str, TeamInvitation] | None:
        if self.invitation is None or self.invitation.token_hash != token:
            return None
        return "Team", "Owner", self.invitation

    async def accept(self, invitation: TeamInvitation, membership: TeamMember) -> bool:
        if invitation.status != InvitationStatus.PENDING:
            return False
        invitation.status = InvitationStatus.ACCEPTED
        invitation.version += 1
        self.accepted_memberships.append(membership)
        return True

    async def get_by_resend_idempotency_key(self, resend_idempotency_key: str) -> None:
        return None


class MemoryMemberRepository:
    async def get_by_team_and_email(self, team_id: UUID, email: str) -> None:
        return None

    async def create(self, team_member: TeamMember) -> TeamMember:
        raise AssertionError("acceptance must use the atomic invitation repository operation")


class MemoryUserRepository:
    def __init__(self, user: User | None = None) -> None:
        self.user = user

    async def get_by_identity(self, issuer: str, subject: str) -> User | None:
        return self.user

    async def create(self, user: User) -> User:
        self.user = user
        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None


class MemoryResendKeyRepository:
    def __init__(self) -> None:
        self.record: InvitationResendIdempotency | None = None

    async def get_by_resend_idempotency_key(
        self, resend_idempotency_key: str
    ) -> InvitationResendIdempotency | None:
        if self.record is not None and self.record.key == resend_idempotency_key:
            return self.record
        return None

    async def create(
        self, invitation: TeamInvitation, resend_idempotency_key: str
    ) -> InvitationResendIdempotency:
        self.record = InvitationResendIdempotency(
            id=uuid4(),
            invitation_id=invitation.id,
            key=resend_idempotency_key,
            created_at=datetime.now(UTC),
        )
        return self.record


def invitation(*, email: str = "invitee@example.com") -> TeamInvitation:
    return TeamInvitation(
        id=uuid4(),
        team_id=uuid4(),
        email=email,
        token_hash=hashlib.sha256(b"token").hexdigest(),
        role="member",
        status=InvitationStatus.PENDING,
        delivery_status=DeliveryStatus.QUEUED,
        delivery_attempts=0,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        idempotency_key="create-key",
        resend_idempotency_keys=[],
        version=1,
    )


def service(
    repository: MemoryInvitationRepository,
    *,
    user: User | None = None,
    resend_repository: MemoryResendKeyRepository | None = None,
) -> TeamInvitationService:
    return TeamInvitationService(
        cast(TeamInvitationRepository, repository),
        cast(TeamRepository, object()),
        cast(TeamMemberRepository, MemoryMemberRepository()),
        cast(UserRepository, MemoryUserRepository(user)),
        cast(
            InvitationResendKeyRepository,
            resend_repository or MemoryResendKeyRepository(),
        ),
    )


@pytest.mark.anyio
async def test_invite_normalizes_email_and_builds_multipart_message() -> None:
    repository = MemoryInvitationRepository()

    created,token = await service(repository).invite_member(
        uuid4(),
        " Invitee@Example.COM ",
        "member",
        "new-key",
    )

    assert created.email == "invitee@example.com"
    assert token

@pytest.mark.anyio
async def test_send_invitation_builds_text_and_html_message() -> None:
    repository = MemoryInvitationRepository()
    sender = FakeMailSender()

    created, token = await service(repository).invite_member(
        uuid4(),
        "Invitee@Example.COM",
        "member",
        "new-key",
    )

    await service(repository).send_invitation_email(
        created,
        token,
        "https://frontend.example.com",
        sender,
    )

    assert len(sender.sent_messages) == 1

    message = sender.sent_messages[0]
    assert message.body
    assert message.html_body is not None


@pytest.mark.anyio
async def test_preview_uses_aware_time_and_never_leaks_token_in_errors() -> None:
    expired = invitation()
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(TeamInvitationExpiredError) as exc_info:
        await service(MemoryInvitationRepository(expired)).get_invitation_preview("token")

    assert "token" not in str(exc_info.value).casefold()


@pytest.mark.anyio
async def test_accept_normalizes_email_and_uses_atomic_repository_operation() -> None:
    pending = invitation(email="invitee@example.com")
    user = User(
        id=uuid4(),
        display_name="Invitee",
        issuer="issuer",
        subject="subject",
        email="Invitee@Example.COM",
        created_at=datetime.now(UTC),
    )
    repository = MemoryInvitationRepository(pending)

    membership = await service(repository, user=user).accept_invitation("token", user.id)

    assert membership.user_id == user.id
    assert repository.accepted_memberships == [membership]
    with pytest.raises(AlreadyConsumedInvitationError):
        await service(repository, user=user).accept_invitation("token", user.id)


@pytest.mark.anyio
async def test_resend_persists_new_token_hash_and_is_idempotent() -> None:
    pending = invitation()

    old_hash = pending.token_hash

    repository = MemoryInvitationRepository(pending)
    resend_repository = MemoryResendKeyRepository()

    invitation_service = service(
        repository,
        resend_repository=resend_repository,
    )

    resent, token = await invitation_service.resend_invitation(
        pending.team_id,
        pending.id,
        "resend-key",
    )

    repeated, repeated_token = await invitation_service.resend_invitation(
        pending.team_id,
        pending.id,
        "resend-key",
    )

    assert resent.token_hash != old_hash
    assert repository.invitation is not None
    assert repository.invitation.token_hash == resent.token_hash
    assert repeated.id == resent.id
