import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.application.ports.invitation_resend_key_repository import InvitationResendKeyRepository
from app.application.ports.team_invitation_repository import TeamInvitationRepository
from app.application.ports.team_member_repository import TeamMemberRepository
from app.application.ports.team_repository import TeamRepository
from app.application.ports.user_repository import UserRepository
from app.application.services.team_invitation_service import (
    AlreadyConsumedInvitationError,
    InvitationIdempotencyConflict,
    InvitationNotPendingError,
    TeamInvitationExpiredError,
    TeamInvitationNotFoundError,
    TeamInvitationService,
)
from app.domain.idempotency import InvitationCreationIdempotency
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import DeliveryStatus, InvitationStatus, TeamInvitation
from app.domain.team_member import TeamMember
from app.domain.user import User
from app.infrastructure.mail import FakeMailSender

ACTOR_ID = uuid4()


class MemoryInvitationRepository:
    def __init__(self, invitation: TeamInvitation | None = None) -> None:
        self.invitation = invitation
        self.records: dict[tuple[UUID, UUID, str, str], InvitationCreationIdempotency] = {}
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

    async def get_creation_idempotency(
        self, actor_id: UUID, team_id: UUID, operation: str, key: str
    ) -> InvitationCreationIdempotency | None:
        return self.records.get((actor_id, team_id, operation, key))

    async def create_with_idempotency(
        self, invitation: TeamInvitation, record: InvitationCreationIdempotency
    ) -> InvitationCreationIdempotency:
        scope = (record.actor_id, record.team_id, record.operation, record.key)
        if scope in self.records:
            return self.records[scope]
        await self.create(invitation, record.key)
        self.records[scope] = record
        return record

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
        self, actor_id: UUID, team_id: UUID, invitation_id: UUID, operation: str, key: str
    ) -> InvitationResendIdempotency | None:
        record = self.record
        if record is not None and (
            record.actor_id,
            record.team_id,
            record.invitation_id,
            record.operation,
            record.key,
        ) == (actor_id, team_id, invitation_id, operation, key):
            return record
        return None

    async def create(
        self, invitation: TeamInvitation, record: InvitationResendIdempotency
    ) -> InvitationResendIdempotency:
        self.record = record
        invitation.version += 1
        return record


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

    result = await service(repository).invite_member(
        uuid4(),
        " Invitee@Example.COM ",
        "member",
        "new-key",
        actor_id=ACTOR_ID,
    )

    assert result.invitation.email == "invitee@example.com"
    assert result.token
    assert result.invitation.token_hash == hashlib.sha256(result.token.encode()).hexdigest()


@pytest.mark.anyio
@pytest.mark.parametrize("delivery_status", list(DeliveryStatus))
async def test_invite_replay_returns_existing_invitation_without_generating_token(
    delivery_status: DeliveryStatus,
) -> None:
    repository = MemoryInvitationRepository()
    created = await service(repository).invite_member(
        uuid4(), "invitee@example.com", "member", "create-key", actor_id=ACTOR_ID
    )
    existing = created.invitation
    existing.delivery_status = delivery_status

    with patch(
        "app.application.services.team_invitation_service.secrets.token_urlsafe"
    ) as generate:
        result = await service(repository).invite_member(
            existing.team_id,
            existing.email,
            existing.role,
            existing.idempotency_key,
            actor_id=ACTOR_ID,
        )

    assert result.invitation is existing
    assert result.token is None
    assert existing.delivery_status == delivery_status
    assert existing.version == 1
    generate.assert_not_called()


@pytest.mark.anyio
async def test_send_invitation_builds_text_and_html_message() -> None:
    repository = MemoryInvitationRepository()
    sender = FakeMailSender()

    result = await service(repository).invite_member(
        uuid4(),
        "Invitee@Example.COM",
        "member",
        "new-key",
        actor_id=ACTOR_ID,
    )

    assert result.token is not None
    await service(repository).send_invitation_email(
        result.invitation,
        result.token,
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
        actor_id=ACTOR_ID,
    )

    repeated, repeated_token = await invitation_service.resend_invitation(
        pending.team_id,
        pending.id,
        "resend-key",
        actor_id=ACTOR_ID,
    )

    assert resent.token_hash != old_hash
    assert repository.invitation is not None
    assert repository.invitation.token_hash == resent.token_hash
    assert repeated.id == resent.id
    assert token
    assert resent.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert repeated_token == ""
    assert repeated.delivery_attempts == 1
    assert repeated.version == 2


@pytest.mark.anyio
@pytest.mark.parametrize("delivery_status", list(DeliveryStatus))
async def test_resend_replay_preserves_token_and_delivery_state(
    delivery_status: DeliveryStatus,
) -> None:
    pending = invitation()
    repository = MemoryInvitationRepository(pending)
    invitation_service = service(repository)
    await invitation_service.resend_invitation(
        pending.team_id, pending.id, "resend-key", actor_id=ACTOR_ID
    )
    pending.delivery_status = delivery_status
    before = replace(pending)

    with patch(
        "app.application.services.team_invitation_service.secrets.token_urlsafe"
    ) as generate:
        repeated, token = await invitation_service.resend_invitation(
            pending.team_id, pending.id, "resend-key", actor_id=ACTOR_ID
        )

    assert repeated == before
    assert token == ""
    generate.assert_not_called()


@pytest.mark.anyio
async def test_creation_hash_uses_normalized_email() -> None:
    repository = MemoryInvitationRepository()
    invitation_service = service(repository)
    team_id = uuid4()
    created = await invitation_service.invite_member(
        team_id, " Invitee@Example.COM ", "member", "key", actor_id=ACTOR_ID
    )
    replay = await invitation_service.invite_member(
        team_id, "invitee@example.com", "member", "key", actor_id=ACTOR_ID
    )
    assert replay.invitation is created.invitation
    assert replay.token is None
    assert len(repository.records) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "role"), [("different@example.com", "member"), ("invitee@example.com", "owner")]
)
async def test_creation_rejects_conflicting_email_or_role(email: str, role: str) -> None:
    repository = MemoryInvitationRepository()
    invitation_service = service(repository)
    team_id = uuid4()
    created = await invitation_service.invite_member(
        team_id, "invitee@example.com", "member", "key", actor_id=ACTOR_ID
    )
    with pytest.raises(InvitationIdempotencyConflict):
        await invitation_service.invite_member(team_id, email, role, "key", actor_id=ACTOR_ID)
    assert repository.invitation is created.invitation
    assert len(repository.records) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("different_scope", ["actor", "team", "operation"])
async def test_creation_keys_are_independent_across_scopes(different_scope: str) -> None:
    repository = MemoryInvitationRepository()
    invitation_service = service(repository)
    team_id = uuid4()
    created = await invitation_service.invite_member(
        team_id, "invitee@example.com", "member", "key", actor_id=ACTOR_ID
    )
    if different_scope == "operation":
        record = repository.records.pop((ACTOR_ID, team_id, "create_invitation", "key"))
        repository.records[(ACTOR_ID, team_id, "other_operation", "key")] = record
    second = await invitation_service.invite_member(
        uuid4() if different_scope == "team" else team_id,
        "other@example.com",
        "member",
        "key",
        actor_id=uuid4() if different_scope == "actor" else ACTOR_ID,
    )
    assert second.invitation.id != created.invitation.id
    assert second.token


@pytest.mark.anyio
async def test_legacy_global_key_is_not_used_for_creation_replay() -> None:
    legacy = invitation()
    repository = MemoryInvitationRepository(legacy)
    result = await service(repository).invite_member(
        uuid4(), "other@example.com", "member", legacy.idempotency_key, actor_id=ACTOR_ID
    )
    assert result.invitation.id != legacy.id
    assert result.token


@pytest.mark.anyio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_creation_race_returns_winner_without_new_token(conflicting: bool) -> None:
    repository = MemoryInvitationRepository()
    invitation_service = service(repository)
    team_id = uuid4()
    created = await invitation_service.invite_member(
        team_id, "invitee@example.com", "member", "key", actor_id=ACTOR_ID
    )
    with patch.object(repository, "get_creation_idempotency", return_value=None):
        if conflicting:
            with pytest.raises(InvitationIdempotencyConflict):
                await invitation_service.invite_member(
                    team_id, "other@example.com", "member", "key", actor_id=ACTOR_ID
                )
        else:
            replay = await invitation_service.invite_member(
                team_id, "invitee@example.com", "member", "key", actor_id=ACTOR_ID
            )
            assert replay.invitation is created.invitation
            assert replay.token is None
    assert repository.invitation is created.invitation


@pytest.mark.anyio
async def test_resend_replay_validates_invitation_ancestry_before_key_lookup() -> None:
    pending = invitation()
    repository = MemoryInvitationRepository(pending)
    keys = MemoryResendKeyRepository()
    invitation_service = service(repository, resend_repository=keys)
    await invitation_service.resend_invitation(
        pending.team_id, pending.id, "key", actor_id=ACTOR_ID
    )
    with patch.object(keys, "get_by_resend_idempotency_key") as lookup:
        with pytest.raises(TeamInvitationNotFoundError):
            await invitation_service.resend_invitation(
                uuid4(), pending.id, "key", actor_id=ACTOR_ID
            )
        lookup.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("status", [InvitationStatus.REVOKED, InvitationStatus.ACCEPTED])
async def test_resend_replay_survives_invitation_status_change(status: InvitationStatus) -> None:
    pending = invitation()
    repository = MemoryInvitationRepository(pending)
    invitation_service = service(repository)
    await invitation_service.resend_invitation(
        pending.team_id, pending.id, "key", actor_id=ACTOR_ID
    )
    pending.status = status
    before = replace(pending)
    repeated, token = await invitation_service.resend_invitation(
        pending.team_id, pending.id, "key", actor_id=ACTOR_ID
    )
    assert repeated == before
    assert token == ""
    with pytest.raises(InvitationNotPendingError):
        await invitation_service.resend_invitation(
            pending.team_id, pending.id, "new-key", actor_id=ACTOR_ID
        )
