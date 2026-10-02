import asyncio
import hashlib
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.team_invitation_service import InvitationPreconditionFailed
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import DeliveryStatus
from app.infrastructure.persistence.configurations import InvitationResendIdempotencyModel
from app.infrastructure.repositories.sqlalchemy_invitation_resend_key_repository import (
    SqlalchemyInvitationResendKeyRepository,
)
from app.infrastructure.repositories.sqlalchemy_team_invitation_repository import (
    SqlAlchemyTeamInvitationRepository,
)
from tests.integration.persistence.test_team_invitation_repository import create_test_invitation
from tests.support.constants import NOW


def record(actor_id: UUID, team_id: UUID, invitation_id: UUID) -> InvitationResendIdempotency:
    return InvitationResendIdempotency(
        id=uuid4(),
        actor_id=actor_id,
        team_id=team_id,
        invitation_id=invitation_id,
        operation="resend_invitation",
        key="key",
        request_hash=hashlib.sha256(b"{}").hexdigest(),
        created_at=NOW,
    )


@pytest.mark.anyio
async def test_lookup_matches_every_scope_component(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    actor_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        invitation = await create_test_invitation(session, team_id)
        await session.commit()
        keys = SqlalchemyInvitationResendKeyRepository(session)
        saved = record(actor_id, team_id, invitation.id)
        await keys.create(invitation, saved)
        scope: list[UUID | str] = [actor_id, team_id, invitation.id, "resend_invitation", "key"]
        assert (
            await keys.get_by_resend_idempotency_key(
                actor_id, team_id, invitation.id, "resend_invitation", "key"
            )
            is not None
        )
        for index in range(len(scope)):
            different = scope.copy()
            different[index] = uuid4() if index < 3 else "other"
            assert (
                await keys.get_by_resend_idempotency_key(
                    UUID(str(different[0])),
                    UUID(str(different[1])),
                    UUID(str(different[2])),
                    str(different[3]),
                    str(different[4]),
                )
                is None
            )
        other_operation = replace(saved, id=uuid4(), operation="other_operation")
        invitation.version += 1
        await keys.create(invitation, other_operation)
        assert (
            await keys.get_by_resend_idempotency_key(
                actor_id, team_id, invitation.id, "other_operation", "key"
            )
            is not None
        )


@pytest.mark.anyio
async def test_stale_invitation_rolls_back_resend_key_and_token(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    actor_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        invitation = await create_test_invitation(session, team_id)
        await session.commit()
        stale = replace(invitation, token_hash="loser-token")
        repository = SqlAlchemyTeamInvitationRepository(session)
        current = await repository.update(invitation)
        keys = SqlalchemyInvitationResendKeyRepository(session)
        with pytest.raises(InvitationPreconditionFailed):
            await keys.create(stale, record(actor_id, team_id, invitation.id))
        assert (
            await session.scalar(select(func.count()).select_from(InvitationResendIdempotencyModel))
            == 0
        )
        assert await repository.get_by_id(invitation.id) == current


@pytest.mark.anyio
async def test_concurrent_duplicate_key_commits_one_rotation(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    actor_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        invitation = await create_test_invitation(session, team_id)
        await session.commit()
    records = [record(actor_id, team_id, invitation.id) for _ in range(2)]

    async def resend(index: int) -> InvitationResendIdempotency:
        async with db_session_factory() as session:
            return await SqlalchemyInvitationResendKeyRepository(session).create(
                replace(
                    invitation,
                    token_hash=f"token-{index}",
                    delivery_attempts=1,
                    delivery_status=DeliveryStatus.QUEUED,
                ),
                records[index],
            )

    first, second = await asyncio.gather(resend(0), resend(1))
    assert first.id == second.id
    winner = 0 if first.id == records[0].id else 1
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation.id)
        assert persisted is not None
        assert persisted.version == 2
        assert persisted.delivery_attempts == 1
        assert persisted.token_hash == f"token-{winner}"
        assert (
            await session.scalar(select(func.count()).select_from(InvitationResendIdempotencyModel))
            == 1
        )
