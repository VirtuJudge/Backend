import hashlib
import re
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
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
    InvitationResendIdempotencyModel,
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
async def test_invitation_resend_replay_never_sends_or_mutates_delivery(
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
    sender = Mock(spec=MailSender)
    sender.send.side_effect = FakeMailSender().send
    app.state.mail_sender = sender
    path = f"/api/v1/teams/{team_id}/invitations"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            path, json={"email": "invitee@example.com"}, headers={"Idempotency-Key": "create-1"}
        )
        assert created.status_code == 201
        invitation_id = UUID(created.json()["id"])
        sender.reset_mock()
        sender.send.side_effect = (
            MailDeliveryError("Delivery failed") if delivery_fails else FakeMailSender().send
        )
        path = f"{path}/{invitation_id}/resend"
        headers = {"Idempotency-Key": "resend-1"}
        resent = await client.post(path, headers=headers)
        assert resent.status_code == 202
        sender.send.assert_called_once()
        async with db_session_factory() as session:
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        assert before is not None
        expected_status = DeliveryStatus.FAILED if delivery_fails else DeliveryStatus.ACCEPTED
        assert before.delivery_status == expected_status
        assert before.delivery_attempts == 2
        message = sender.send.call_args.args[0]
        link = re.search(r"https://frontend\.example\.com/invitations/([\w-]+)", message.body)
        assert link is not None
        token = link.group(1)
        assert before.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert message.html_body is not None and link.group(0) in message.html_body

        for _ in range(2):
            replay = await client.post(path, headers=headers)
            assert replay.status_code == 202
            assert replay.json()["id"] == resent.json()["id"]
            assert replay.json()["delivery_status"] == (
                "failed" if delivery_fails else "accepted_by_provider"
            )
            assert replay.json()["delivery_attempts"] == 2
            assert replay.json()["version"] == before.version
            assert replay.headers["ETag"] == f'"{replay.json()["etag"]}"'
            assert "token" not in replay.json() and "token_hash" not in replay.json()
            assert token not in replay.text
            sender.send.assert_called_once()

        async with db_session_factory() as session:
            after = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
            count = await session.scalar(
                select(func.count()).select_from(InvitationResendIdempotencyModel)
            )
        assert after == before
        assert count == 1

        fresh = await client.post(path, headers={"Idempotency-Key": "resend-2"})
        assert fresh.status_code == 202
        assert sender.send.call_count == 2
        async with db_session_factory() as session:
            after = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        assert after is not None
        assert after.delivery_attempts == 3
        assert after.token_hash != before.token_hash


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["invitation", "team", "actor"])
async def test_resend_key_collision_never_returns_another_invitation(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    scope: str,
) -> None:
    user_id, team_id, _ = seed_db
    async with db_session_factory() as session:
        await session.execute(
            update(TeamMemberModel)
            .where(TeamMemberModel.user_id == user_id, TeamMemberModel.team_id == team_id)
            .values(role="owner")
        )
        await session.commit()
    other_team_id, other_user_id = uuid4(), uuid4()
    async with db_session_factory() as session:
        session.add(TeamModel(id=other_team_id, name="Other Team", created_at=NOW))
        session.add(
            UserModel(
                id=other_user_id,
                email="other-owner@example.com",
                issuer="test",
                subject="other-owner",
                created_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            [
                TeamMemberModel(
                    team_id=other_team_id, user_id=user_id, role="owner", joined_at=NOW
                ),
                TeamMemberModel(
                    team_id=team_id, user_id=other_user_id, role="owner", joined_at=NOW
                ),
            ]
        )
        await session.commit()
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    app.state.session_factory = db_session_factory
    app.state.redis = AsyncMock(spec=RateLimiter)
    user = User(user_id, "Owner", "test", "owner", "owner@example.com", NOW)
    app.dependency_overrides[get_current_user] = lambda: user
    sender = Mock(spec=MailSender)
    sender.send.side_effect = FakeMailSender().send
    app.state.mail_sender = sender
    path = f"/api/v1/teams/{team_id}/invitations"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invitations = []
        for index in range(2):
            created = await client.post(
                f"/api/v1/teams/{other_team_id}/invitations"
                if scope == "team" and index == 1
                else path,
                json={"email": f"invitee{index}@example.com"},
                headers={"Idempotency-Key": f"create-{index}"},
            )
            assert created.status_code == 201
            invitations.append(created.json()["id"])
        if scope == "actor":
            invitations[1] = invitations[0]
        sender.reset_mock()
        for index, invitation_id in enumerate(invitations):
            if scope == "actor" and index == 1:
                other = User(
                    other_user_id, "Other", "test", "other-owner", "other-owner@example.com", NOW
                )
                app.dependency_overrides[get_current_user] = lambda other=other: other
            target_team = other_team_id if scope == "team" and index == 1 else team_id
            resent = await client.post(
                f"/api/v1/teams/{target_team}/invitations/{invitation_id}/resend",
                headers={"Idempotency-Key": "shared-key"},
            )
            assert resent.status_code == 202
            assert resent.json()["id"] == invitation_id
            replay = await client.post(
                f"/api/v1/teams/{target_team}/invitations/{invitation_id}/resend",
                headers={"Idempotency-Key": "shared-key"},
            )
            assert replay.status_code == 202
            assert replay.json()["id"] == invitation_id
        assert sender.send.call_count == 2
        async with db_session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(InvitationResendIdempotencyModel)
            )
        assert count == 2

        app.dependency_overrides[get_current_user] = lambda: user
        for target_team, invitation_id in [(team_id, uuid4()), (other_team_id, invitations[0])]:
            missing = await client.post(
                f"/api/v1/teams/{target_team}/invitations/{invitation_id}/resend",
                headers={"Idempotency-Key": "shared-key"},
            )
            assert missing.status_code == 404
            assert "email" not in missing.json()
        assert sender.send.call_count == 2

        async with db_session_factory() as session:
            await session.execute(
                update(InvitationResendIdempotencyModel)
                .where(InvitationResendIdempotencyModel.actor_id == user_id)
                .values(request_hash="different")
            )
            await session.commit()
        conflict = await client.post(
            f"{path}/{invitations[0]}/resend", headers={"Idempotency-Key": "shared-key"}
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "idempotency_conflict"
        assert sender.send.call_count == 2

        outsider = User(
            other_user_id, "Other", "test", "other-owner", "other-owner@example.com", NOW
        )
        app.dependency_overrides[get_current_user] = lambda: outsider
        forbidden = await client.post(
            f"/api/v1/teams/{other_team_id}/invitations/{invitations[0]}/resend",
            headers={"Idempotency-Key": "shared-key"},
        )
        assert forbidden.status_code == 403
        assert sender.send.call_count == 2
