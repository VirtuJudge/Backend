from dataclasses import replace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import get_current_user
from app.domain.team_invitation import InvitationStatus, TeamInvitation
from app.domain.user import User
from app.infrastructure.persistence.configurations import (
    TeamInvitationModel,
    TeamMemberModel,
    TeamModel,
)
from app.infrastructure.repositories.sqlalchemy_team_invitation_repository import (
    SqlAlchemyTeamInvitationRepository,
)
from tests.integration.api.test_invitation_create_api import invitation_app
from tests.support.constants import NOW


@pytest.mark.anyio
async def test_revoke_requires_current_etag_and_preserves_rejected_invitation(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, sender = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            path, json={"email": "invitee@example.com"}, headers={"Idempotency-Key": "create"}
        )
        assert created.status_code == 201
        invitation_id = UUID(created.json()["id"])
        async with db_session_factory() as session:
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        assert before is not None
        listed = await client.get(path)
        current_etag = f'"{listed.json()["items"][0]["etag"]}"'
        assert listed.json()["items"][0]["version"] == before.version

        for headers, status in [
            ({}, 422),
            ({"If-Match": ""}, 422),
            ({"If-Match": " "}, 412),
            ({"If-Match": "*"}, 412),
            ({"If-Match": '"*"'}, 412),
            ({"If-Match": '"stale"'}, 412),
            ({"If-Match": created.headers["ETag"]}, 412),
            ({"If-Match": f"W/{current_etag}"}, 412),
        ]:
            rejected = await client.delete(f"{path}/{invitation_id}", headers=headers)
            assert rejected.status_code == status
            async with db_session_factory() as session:
                assert (
                    await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
                    == before
                )
        revoked = await client.delete(f"{path}/{invitation_id}", headers={"If-Match": current_etag})
        assert revoked.status_code == 204
        assert revoked.content == b""
    async with db_session_factory() as session:
        after = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
    assert after == replace(before, status=InvitationStatus.REVOKED, version=before.version + 1)
    assert len(sender.sent_messages) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status", [InvitationStatus.ACCEPTED, InvitationStatus.REVOKED, InvitationStatus.EXPIRED]
)
async def test_revoke_preserves_terminal_state_conflicts(
    db_session_factory: async_sessionmaker[AsyncSession],
    seed_db: tuple[UUID, UUID, UUID],
    status: InvitationStatus,
) -> None:
    user_id, team_id, _ = seed_db
    app, _ = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            path, json={"email": "invitee@example.com"}, headers={"Idempotency-Key": "create"}
        )
        invitation_id = UUID(created.json()["id"])
        async with db_session_factory() as session:
            await session.execute(
                update(TeamInvitationModel)
                .where(TeamInvitationModel.id == invitation_id)
                .values(status=status)
            )
            await session.commit()
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        listed = await client.get(path)
        response = await client.delete(
            f"{path}/{invitation_id}",
            headers={"If-Match": f'"{listed.json()["items"][0]["etag"]}"'},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "already_consumed" if status == InvitationStatus.ACCEPTED else "invitation_not_pending"
    )
    async with db_session_factory() as session:
        assert await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id) == before


@pytest.mark.anyio
async def test_revoke_checks_team_owner_and_invitation_ancestry(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, _ = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            path, json={"email": "invitee@example.com"}, headers={"Idempotency-Key": "create"}
        )
        invitation_id = UUID(created.json()["id"])
        listed = await client.get(path)
        headers = {"If-Match": f'"{listed.json()["items"][0]["etag"]}"'}
        async with db_session_factory() as session:
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
            other_team_id = uuid4()
            session.add(TeamModel(id=other_team_id, name="Other Team", created_at=NOW))
            await session.flush()
            await session.execute(
                update(TeamInvitationModel)
                .where(TeamInvitationModel.id == invitation_id)
                .values(team_id=other_team_id)
            )
            await session.commit()
        wrong_team = await client.delete(f"{path}/{invitation_id}", headers=headers)
        missing = await client.delete(f"{path}/{uuid4()}", headers=headers)
        assert wrong_team.status_code == missing.status_code == 404
        async with db_session_factory() as session:
            await session.execute(
                update(TeamInvitationModel)
                .where(TeamInvitationModel.id == invitation_id)
                .values(team_id=team_id)
            )
            await session.execute(
                update(TeamMemberModel)
                .where(TeamMemberModel.user_id == user_id)
                .values(role="member")
            )
            await session.commit()
        member = await client.delete(f"{path}/{invitation_id}", headers=headers)
        assert member.status_code == 403
        outsider = User(uuid4(), "Outsider", "test", "outsider", "outsider@example.com", NOW)
        app.dependency_overrides[get_current_user] = lambda: outsider
        denied = await client.delete(f"{path}/{invitation_id}", headers=headers)
        assert denied.status_code == 403
    async with db_session_factory() as session:
        assert await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id) == before


@pytest.mark.anyio
async def test_revoke_returns_412_if_version_changes_before_update(
    db_session_factory: async_sessionmaker[AsyncSession], seed_db: tuple[UUID, UUID, UUID]
) -> None:
    user_id, team_id, _ = seed_db
    app, _ = await invitation_app(db_session_factory, user_id, team_id)
    path = f"/api/v1/teams/{team_id}/invitations"
    original_update = SqlAlchemyTeamInvitationRepository.update

    async def concurrent_update(
        repository: SqlAlchemyTeamInvitationRepository, invitation: TeamInvitation
    ) -> TeamInvitation:
        async with db_session_factory() as session:
            await session.execute(
                update(TeamInvitationModel)
                .where(TeamInvitationModel.id == invitation.id)
                .values(version=invitation.version + 1, delivery_attempts=1)
            )
            await session.commit()
        return await original_update(repository, invitation)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            path, json={"email": "invitee@example.com"}, headers={"Idempotency-Key": "create"}
        )
        invitation_id = UUID(created.json()["id"])
        async with db_session_factory() as session:
            before = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
        assert before is not None
        listed = await client.get(path)
        with patch.object(SqlAlchemyTeamInvitationRepository, "update", concurrent_update):
            response = await client.delete(
                f"{path}/{invitation_id}",
                headers={"If-Match": f'"{listed.json()["items"][0]["etag"]}"'},
            )
    assert response.status_code == 412
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "version precondition failed"
    async with db_session_factory() as session:
        after = await SqlAlchemyTeamInvitationRepository(session).get_by_id(invitation_id)
    assert after == replace(before, version=before.version + 1, delivery_attempts=1)
