from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.domain.team_invitation import (
    DeliveryStatus,
    InvitationStatus,
    TeamInvitation,
)
from app.infrastructure.persistence.configurations.invitationResendIdompotancyConfiguration import (
    InvitationResendIdempotencyModel,
)
from app.infrastructure.persistence.configurations.teamConfigration import TeamModel
from app.infrastructure.persistence.configurations.teamInvitationConfigurations import (
    TeamInvitationModel,
)
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel
from app.infrastructure.persistence.configurations.userConfigration import UserModel
from app.infrastructure.repositories.sqlalchemyTeamInvitationRepository import (
    SqlAlchemyTeamInvitationRepository,
)


async def create_test_invitation(
    session: AsyncSession,
    team_id: UUID,
    email: str = "test@example.com",
    status: InvitationStatus = InvitationStatus.PENDING,
) -> TeamInvitation:
    repository = SqlAlchemyTeamInvitationRepository(session)

    invitation = TeamInvitation(
        id=uuid4(),
        team_id=team_id,
        email=email,
        token_hash=f"test-token-{uuid4().hex}",
        role="member",
        status=status,
        delivery_status=DeliveryStatus.QUEUED,
        delivery_attempts=0,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        idempotency_key=f"key-{uuid4()}",
        resend_idempotency_keys=[],
        version=1,
    )

    return await repository.create(
        invitation,
        invitation.idempotency_key,
    )


@pytest.mark.anyio
async def test_create_persists_invitation(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )

    session.add(team)
    await session.commit()

    repository = SqlAlchemyTeamInvitationRepository(session)

    invitation = TeamInvitation(
        id=uuid4(),
        team_id=team.id,
        email="user@example.com",
        token_hash="hash-123",
        role="member",
        status=InvitationStatus.PENDING,
        delivery_status=DeliveryStatus.QUEUED,
        delivery_attempts=0,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        idempotency_key="key-123",
        resend_idempotency_keys=[],
        version=1,
    )

    result = await repository.create(
        invitation,
        invitation.idempotency_key,
    )

    persisted = await session.scalar(
        select(TeamInvitationModel).where(TeamInvitationModel.id == invitation.id)
    )

    assert result.id == invitation.id
    assert result.token_hash == invitation.token_hash
    assert persisted is not None
    assert persisted.token_hash == invitation.token_hash


@pytest.mark.anyio
async def test_get_by_id_returns_invitation(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )

    session.add(team)
    await session.commit()

    repository = SqlAlchemyTeamInvitationRepository(session)

    invitation = await create_test_invitation(
        session,
        team.id,
    )

    result = await repository.get_by_id(invitation.id)

    assert result is not None
    assert result.id == invitation.id
    assert result.token_hash == invitation.token_hash


@pytest.mark.anyio
async def test_get_by_id_returns_none_when_missing(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()
    repository = SqlAlchemyTeamInvitationRepository(session)

    result = await repository.get_by_id(uuid4())

    assert result is None


@pytest.mark.anyio
async def test_update_changes_status_and_increments_version(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)
    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()
    invitation = await create_test_invitation(
        session,
        team_id=team.id,
    )

    original_version = invitation.version

    invitation.status = InvitationStatus.ACCEPTED

    updated = await repository.update(invitation)

    assert updated.status == InvitationStatus.ACCEPTED
    assert updated.version == original_version + 1


@pytest.mark.anyio
async def test_get_by_idempotency_key_returns_invitation(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    invitation = await create_test_invitation(
        session,
        team_id=team.id,
    )

    result = await repository.get_by_idempotency_key(invitation.idempotency_key)

    assert result is not None
    assert result.id == invitation.id


@pytest.mark.anyio
async def test_get_by_idempotency_key_returns_returns_none_when_missing(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    result = await repository.get_by_idempotency_key("non-existent-key")

    assert result is None


@pytest.mark.anyio
async def test_list_by_team_returns_invitations(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    first = await create_test_invitation(session, team_id=team.id)
    second = await create_test_invitation(session, team_id=team.id)

    invitations, next_cursor = await repository.list_by_team(
        team.id,
        limit=20,
    )

    assert [i.id for i in invitations] == sorted([first.id, second.id])
    assert next_cursor is None


@pytest.mark.anyio
async def test_list_by_team_supports_cursor(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    first = await create_test_invitation(session, team_id=team.id)
    second = await create_test_invitation(session, team_id=team.id)

    invitations, next_cursor = await repository.list_by_team(
        team.id,
        limit=1,
    )

    assert len(invitations) == 1
    assert invitations[0].id == min(first.id, second.id)
    assert next_cursor is not None

    next_page, next_cursor = await repository.list_by_team(
        team.id,
        cursor=next_cursor,
        limit=1,
    )

    assert len(next_page) == 1
    assert next_page[0].id == max(first.id, second.id)
    assert next_cursor is None


@pytest.mark.anyio
async def test_get_invitation_by_token_returns_team_and_owner(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    user = UserModel(
        id=uuid4(),
        issuer="test-issuer",
        subject=f"subject-{uuid4()}",
        email="owner@example.com",
        display_name="Team Owner",
        created_at=datetime.now(UTC),
    )

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )

    session.add_all([user, team])
    await session.commit()

    owner_membership = TeamMemberModel(
        id=uuid4(), team_id=team.id, user_id=user.id, role="owner", joined_at=datetime.now(UTC)
    )

    session.add(owner_membership)
    await session.commit()

    invitation = await create_test_invitation(
        session,
        team_id=team.id,
        email="invitee@example.com",
    )

    result = await repository.get_invitation_by_token(
        invitation.token_hash,
    )

    assert result is not None

    team_name, owner_name, returned_invitation = result

    assert team_name == team.name
    assert owner_name == "Team Owner"
    assert returned_invitation.id == invitation.id
    assert returned_invitation.token_hash == invitation.token_hash


@pytest.mark.anyio
async def test_get_invitation_by_token_returns_none_when_not_found(
    async_db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyTeamInvitationRepository(async_db_session)

    result = await repository.get_invitation_by_token("non-existent-token")

    assert result is None


@pytest.mark.anyio
async def test_get_by_resend_idempotency_key_returns_invitation(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    invitation = await create_test_invitation(
        session,
        team_id=team.id,
    )

    resend_key = f"resend-key-{uuid4().hex}"

    resend_idempotency = InvitationResendIdempotencyModel(
        id=uuid4(),
        invitation_id=invitation.id,
        key=resend_key,
        created_at=datetime.now(UTC),
    )

    session.add(resend_idempotency)
    await session.commit()

    result = await repository.get_by_resend_idempotency_key(resend_key)

    assert result is not None
    assert result.id == invitation.id
    assert result.team_id == invitation.team_id
    assert result.email == invitation.email


@pytest.mark.anyio
async def test_update_rejects_stale_version(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyTeamInvitationRepository(session)

    team = TeamModel(
        id=uuid4(),
        name=f"Team-{uuid4().hex[:8]}",
        created_at=datetime.now(UTC),
    )
    session.add(team)
    await session.commit()

    invitation = await create_test_invitation(
        session,
        team_id=team.id,
    )

    invitation.status = InvitationStatus.ACCEPTED
    await repository.update(invitation)

    invitation.status = InvitationStatus.REVOKED

    with pytest.raises(ValueError) as exc_info:
        await repository.update(invitation)

    assert "Invitation with ID" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_by_resend_idempotency_key_returns_none_when_missing(
    async_db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyTeamInvitationRepository(async_db_session)

    result = await repository.get_by_resend_idempotency_key("non-existent-resend-key")

    assert result is None
