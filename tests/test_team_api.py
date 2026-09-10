from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.services import get_team_service
from app.api.dependencies.team_authorization import get_team_member
from app.application.ports.team_repository import TeamRepository
from app.application.services.team_service import TeamService
from app.domain.idempotency import TeamCreationIdempotency
from app.domain.team import Team
from app.domain.team_member import TeamMember
from app.main import create_app
from app.settings import Settings

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
        async def list_members(
            self,
            team_id: UUID,
            cursor: UUID | None = None,
            limit: int = 50,
        ) -> tuple[list[TeamMember], UUID | None]:
            return [member_1, member_2], None

        async def get_membership(self, team_id: UUID, user_id: UUID) -> TeamMember | None:
            return member_1

        async def is_member(self, team_id: UUID, user_id: UUID) -> bool:
            return True

        async def is_owner(self, team_id: UUID, user_id: UUID) -> bool:
            return True

        async def list_for_user(
            self,
            user_id: UUID,
            cursor: UUID | None = None,
            limit: int = 50,
        ) -> tuple[list[Team], UUID | None]:
            return [], None

        async def get_by_id(self, team_id: UUID) -> Team | None:
            return None

        async def get_by_name(self, name: str) -> Team | None:
            return None

        async def create(self, team: Team, owner: TeamMember) -> Team:
            return team

        async def get_creation_idempotency(
            self, user_id: UUID, key: str
        ) -> TeamCreationIdempotency | None:
            return None

        async def save_creation_idempotency(self, record: TeamCreationIdempotency) -> None:
            pass

        async def update_name(self, team_id: UUID, name: str, expected_version: int) -> Team | None:
            return None

        async def delete_member(self, team_id: UUID, user_id: UUID) -> bool:
            return True

        async def transfer_ownership(
            self,
            team_id: UUID,
            current_owner_id: UUID,
            new_owner_id: UUID,
        ) -> bool:
            return True

    fake_repo = FakeTeamRepo()
    team_service = TeamService(fake_repo)

    async def get_fake_team_service() -> TeamService:
        return team_service

    async def get_fake_team_member() -> TeamMember:
        return member_1

    app.dependency_overrides[get_team_service] = get_fake_team_service
    app.dependency_overrides[get_team_member] = get_fake_team_member

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
