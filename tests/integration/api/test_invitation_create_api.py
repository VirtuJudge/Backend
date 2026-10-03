import asyncio
import hashlib
import re
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import get_current_user
from app.application.mail import MailDeliveryError, MailSender
from app.application.ports.rate_limiter import RateLimiter
from app.domain.team_invitation import DeliveryStatus
from app.domain.user import User
from app.infrastructure.mail import FakeMailSender
from app.infrastructure.persistence.configurations import (
    InvitationCreationIdempotencyModel,
    TeamInvitationModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.repositories.sqlalchemy_team_invitation_repository import (
    SqlAlchemyTeamInvitationRepository,
)
from app.main import create_app
from app.settings import Settings
from tests.support.constants import NOW


@pytest.mark.anyio
@pytest.mark.parametrize("delivery_fails", [False, True])
async def test_invitation_create_replay_never_sends_or_mutates_delivery(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    delivery_fails: bool,
) -> None:
    user_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        await session.execute(
            update(TeamMemberModel)
            .where(TeamMemberModel.user_id == user_id, TeamMemberModel.team_id == team_id)
            .values(role="owner")
        )
        await session.commit()

    app = create_app(
        Settings(
            app_env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            frontend_url="https://frontend.example.com",
            mail_backend="fake",
        )
    )
    app.state.session_factory = db_session_factory
    app.state.redis = AsyncMock(spec=RateLimiter)
    user = User(user_id, "Owner", "test", "owner", "owner@example.com", NOW)
    app.dependency_overrides[get_current_user] = lambda: user
    mail = FakeMailSender()
    sender = Mock(spec=MailSender)
    sender.send.side_effect = MailDeliveryError("Delivery failed") if delivery_fails else mail.send
    app.state.mail_sender = sender
    path = f"/api/v1/teams/{team_id}/invitations"
    headers = {"Idempotency-Key": "invite-1"}
    payload = {"email": "invitee@example.com", "role": "member"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(path, json=payload, headers=headers)
        assert created.status_code == 201
        sender.send.assert_called_once()
        invitation_id = UUID(created.json()["id"])
        async with db_session_factory() as session:
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        assert before is not None
        expected_status = DeliveryStatus.FAILED if delivery_fails else DeliveryStatus.ACCEPTED
        assert before.delivery_status == expected_status
        assert before.delivery_attempts == 1
        message = sender.send.call_args.args[0]
        link = re.search(r"https://frontend\.example\.com/invitations/([\w-]+)", message.body)
        assert link is not None
        token = link.group(1)
        assert before.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert message.html_body is not None and link.group(0) in message.html_body

        for _ in range(2):
            replay = await client.post(path, json=payload, headers=headers)
            assert replay.status_code == 201
            assert replay.json()["id"] == created.json()["id"]
            assert replay.json()["delivery_status"] == expected_status
            assert replay.json()["delivery_attempts"] == 1
            assert replay.json()["version"] == before.version
            assert replay.headers["ETag"] == f'"{replay.json()["etag"]}"'
            assert "token" not in replay.json() and "token_hash" not in replay.json()
            assert token not in replay.text
            sender.send.assert_called_once()

    async with db_session_factory() as session:
        after = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        count = await session.scalar(select(func.count()).select_from(TeamInvitationModel))
    assert after == before
    assert count == 1


async def invitation_app(
    factory: async_sessionmaker[AsyncSession], user_id: UUID, team_id: UUID
) -> tuple[FastAPI, FakeMailSender]:
    async with factory() as session:
        await session.execute(
            update(TeamMemberModel)
            .where(TeamMemberModel.user_id == user_id, TeamMemberModel.team_id == team_id)
            .values(role="owner")
        )
        await session.commit()
    app = create_app(
        Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:", mail_backend="fake")
    )
    app.state.session_factory = factory
    app.state.redis = AsyncMock(spec=RateLimiter)
    user = User(user_id, "Owner", "test", "owner", "owner@example.com", NOW)
    app.dependency_overrides[get_current_user] = lambda: user
    sender = FakeMailSender()
    app.state.mail_sender = sender
    return app, sender


@pytest.mark.anyio
async def test_invitation_key_conflicts_are_safe_and_do_not_send_mail(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, sender = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    headers = {"Idempotency-Key": "same-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(path, json={"email": "Invitee@Example.COM"}, headers=headers)
        replay = await client.post(
            path, json={"email": "invitee@example.com", "role": "member"}, headers=headers
        )
        conflict = await client.post(path, json={"email": "other@example.com"}, headers=headers)
    assert created.status_code == replay.status_code == 201
    assert created.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    assert conflict.headers["content-type"] == "application/problem+json"
    assert conflict.json()["detail"] == "idempotency_key_reused"
    assert "invitee@example.com" not in conflict.text
    assert "other@example.com" not in conflict.text
    assert len(sender.sent_messages) == 1
    async with db_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TeamInvitationModel)) == 1


@pytest.mark.anyio
async def test_invitation_key_reuse_cannot_return_another_teams_invitation(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, sender = await invitation_app(db_session_factory, user_id, team_id)
    second_team_id = uuid4()
    async with db_session_factory() as session:
        session.add(TeamModel(id=second_team_id, name="Second Team", created_at=NOW))
        await session.flush()
        session.add(
            TeamMemberModel(team_id=second_team_id, user_id=user_id, role="owner", joined_at=NOW)
        )
        await session.commit()
    headers = {"Idempotency-Key": "same-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            f"/api/v1/teams/{team_id}/invitations",
            json={"email": "first@example.com"},
            headers=headers,
        )
        second = await client.post(
            f"/api/v1/teams/{second_team_id}/invitations",
            json={"email": "second@example.com"},
            headers=headers,
        )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["team_id"] == str(second_team_id)
    assert second.json()["email"] == "second@example.com"
    assert len(sender.sent_messages) == 2


@pytest.mark.anyio
async def test_invitation_keys_are_scoped_to_actor_after_ownership_transfer(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, sender = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    headers = {"Idempotency-Key": "same-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(path, json={"email": "first@example.com"}, headers=headers)
        second_user_id = uuid4()
        async with db_session_factory() as session:
            session.add(
                UserModel(id=second_user_id, issuer="test", subject="second", created_at=NOW)
            )
            await session.flush()
            await session.execute(
                update(TeamMemberModel)
                .where(TeamMemberModel.user_id == user_id)
                .values(role="member")
            )
            session.add(
                TeamMemberModel(
                    team_id=team_id, user_id=second_user_id, role="owner", joined_at=NOW
                )
            )
            await session.commit()
        forbidden = await client.post(path, json={"email": "first@example.com"}, headers=headers)
        assert forbidden.status_code == 403
        user = User(second_user_id, "New Owner", "test", "second", "owner2@example.com", NOW)
        app.dependency_overrides[get_current_user] = lambda: user
        second = await client.post(path, json={"email": "second@example.com"}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert len(sender.sent_messages) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("same_payload", [True, False])
async def test_concurrent_invitation_creation_converges_on_one_scoped_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    same_payload: bool,
) -> None:
    user_id, team_id, _ = seed_db
    app, sender = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    headers = {"Idempotency-Key": "same-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            client.post(path, json={"email": "first@example.com"}, headers=headers),
            client.post(
                path,
                json={"email": "first@example.com" if same_payload else "other@example.com"},
                headers=headers,
            ),
        )
    assert sorted(response.status_code for response in responses) == (
        [201, 201] if same_payload else [201, 409]
    )
    if same_payload:
        assert responses[0].json()["id"] == responses[1].json()["id"]
    else:
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["detail"] == "idempotency_key_reused"
    assert len(sender.sent_messages) == 1
    async with db_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TeamInvitationModel)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(InvitationCreationIdempotencyModel)
            )
            == 1
        )
