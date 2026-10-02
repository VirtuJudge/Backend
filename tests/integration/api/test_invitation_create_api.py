import hashlib
import re
from unittest.mock import AsyncMock, Mock
from uuid import UUID

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
from app.infrastructure.persistence.configurations import TeamInvitationModel, TeamMemberModel
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
