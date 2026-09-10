from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.services import get_team_service
from app.api.dependencies.teamAuthorization import get_team_member
from app.application.interfaces.teamRepository import TeamRepository
from app.application.services.teamService import TeamService
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.infrastructure.settings import Settings
from app.main import create_app

NOW = datetime.now(UTC)


@pytest.mark.anyio
async def test_list_team_members_api_returns_display_name() -> None:
    settings = Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)

    team_id = uuid4()
    user_1_id = uuid4()
    user_2_id = uuid4()

    member_1 = TeamMember(
        id=uuid4(),
        team_id=team_id,
        user_id=user_1_id,
        role="owner",
        joined_at=NOW,
        display_name="Ahmed El-Sherbiny",
    )
    member_2 = TeamMember(
        id=uuid4(),
        team_id=team_id,
        user_id=user_2_id,
        role="member",
        joined_at=NOW,
        display_name="Sarah Chen",
    )

    class FakeTeamRepo(TeamRepository):
        async def list_members(self, t_id, cursor=None, limit=50):
            return [member_1, member_2], None
        async def get_membership(self, t_id, u_id):
            return member_1
        async def is_member(self, t_id, u_id):
            return True
        async def is_owner(self, t_id, u_id):
            return True
        async def list_for_user(self, user_id, cursor=None, limit=50):
            return [], None
        async def get_by_id(self, team_id):
            return None
        async def get_by_name(self, name):
            return None
        async def create(self, team, owner):
            return team
        async def get_creation_idempotency(self, user_id, key):
            return None
        async def save_creation_idempotency(self, idempotency):
            pass
        async def update_name(self, team_id, name, version):
            return None
        async def delete_member(self, team_id, user_id):
            return True
        async def transfer_ownership(self, team_id, current_owner_id, new_owner_id):
            return True

    fake_repo = FakeTeamRepo()
    team_service = TeamService(fake_repo)

    app.dependency_overrides[get_team_service] = lambda: team_service
    app.dependency_overrides[get_team_member] = lambda: member_1

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/teams/{team_id}/members")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 2
    assert data["items"][0]["display_name"] == "Ahmed El-Sherbiny"
    assert data["items"][0]["role"] == "owner"
    assert data["items"][1]["display_name"] == "Sarah Chen"
    assert data["items"][1]["role"] == "member"
