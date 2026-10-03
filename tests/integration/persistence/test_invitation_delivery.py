import asyncio
from dataclasses import replace
from threading import Event
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.mail import MailDeliveryError, MailSender
from app.application.services.team_invitation_service import InvitationPreconditionFailed
from app.domain.team_invitation import DeliveryStatus
from app.infrastructure.mail import FakeMailSender
from app.infrastructure.repositories.sqlalchemy_team_invitation_repository import (
    SqlAlchemyTeamInvitationRepository,
)
from app.main import team_invitation_service_factory
from tests.integration.persistence.test_team_invitation_repository import create_test_invitation


@pytest.mark.anyio
@pytest.mark.parametrize("delivery_fails", [False, True])
async def test_attempt_is_committed_before_mail_and_preserved_after_delivery(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    delivery_fails: bool,
) -> None:
    _, team_id, _ = seed_db
    async with db_session_factory() as session:
        pending = await create_test_invitation(session, team_id)
        await session.commit()

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = Event()
    sender = Mock(spec=MailSender)

    def send(_message: object) -> None:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=10)
        if delivery_fails:
            raise MailDeliveryError("Delivery failed")

    sender.send.side_effect = send
    async with db_session_factory() as session:
        delivery = asyncio.create_task(
            team_invitation_service_factory(session).send_invitation_email(
                pending, "token", "https://frontend.example.com", sender
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=10)
            async with db_session_factory() as observer:
                persisted = await SqlAlchemyTeamInvitationRepository(observer).get_by_id(pending.id)
            assert persisted is not None
            assert persisted.delivery_attempts == 1
            assert persisted.delivery_status == DeliveryStatus.QUEUED
            assert persisted.version == 2
        finally:
            release.set()
            await delivery

    sender.send.assert_called_once()
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
    assert persisted is not None
    assert persisted.delivery_attempts == 1
    assert persisted.delivery_status == (
        DeliveryStatus.FAILED if delivery_fails else DeliveryStatus.ACCEPTED
    )
    assert persisted.version == 3
    assert persisted.token_hash == pending.token_hash
    assert persisted.status == pending.status


@pytest.mark.anyio
async def test_competing_delivery_starts_do_not_lose_or_double_count_attempts(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    _, team_id, _ = seed_db
    async with db_session_factory() as session:
        pending = await create_test_invitation(session, team_id)
        await session.commit()
    sender = FakeMailSender()

    async def deliver() -> None:
        async with db_session_factory() as session:
            await team_invitation_service_factory(session).send_invitation_email(
                replace(pending), "token", "https://frontend.example.com", sender
            )

    results = await asyncio.gather(deliver(), deliver(), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, InvitationPreconditionFailed) for result in results) == 1
    assert len(sender.sent_messages) == 1
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
        assert persisted is not None
        assert persisted.delivery_attempts == 1
        assert persisted.delivery_status == DeliveryStatus.ACCEPTED
        await team_invitation_service_factory(session).send_invitation_email(
            persisted, "token", "https://frontend.example.com", sender
        )
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
    assert persisted is not None
    assert persisted.delivery_attempts == len(sender.sent_messages) == 2


@pytest.mark.anyio
async def test_attempt_commit_failure_does_not_send_mail_or_change_persisted_count(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
) -> None:
    _, team_id, _ = seed_db
    async with db_session_factory() as session:
        pending = await create_test_invitation(session, team_id)
        await session.commit()
        before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
    assert before is not None
    sender = FakeMailSender()
    async with db_session_factory() as session:
        with (
            patch.object(session, "commit", side_effect=RuntimeError("Commit failed")),
            pytest.raises(RuntimeError, match="Commit failed"),
        ):
            await team_invitation_service_factory(session).send_invitation_email(
                pending, "token", "https://frontend.example.com", sender
            )
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
    assert persisted == before
    assert sender.sent_messages == []


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["gmail", "resend"])
async def test_real_mail_adapters_persist_provider_neutral_acceptance(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    provider: str,
) -> None:
    from pydantic import SecretStr

    from app.infrastructure.mail import GmailMailSender, ResendMailSender

    _, team_id, _ = seed_db
    sender = (
        GmailMailSender("smtp.example.com", 587, "user", SecretStr("test"), "sender@example.com")
        if provider == "gmail"
        else ResendMailSender("https://mail.example.com", SecretStr("test"), "sender@example.com")
    )
    with patch("app.infrastructure.mail.smtplib.SMTP"), patch("app.infrastructure.mail.httpx.post"):
        async with db_session_factory() as session:
            pending = await create_test_invitation(session, team_id)
            await session.commit()
            await team_invitation_service_factory(session).send_invitation_email(
                pending, "token", "https://frontend.example.com", sender
            )
    async with db_session_factory() as session:
        persisted = await SqlAlchemyTeamInvitationRepository(session).get_by_id(pending.id)
    assert persisted is not None
    assert persisted.delivery_status.value == "accepted_by_provider"
    assert persisted.delivery_attempts == 1
