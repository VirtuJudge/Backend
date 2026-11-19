from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.ports.user_repository import UserRepository
from app.application.services.user_service import UserService
from app.domain.user import User


class _Repository(UserRepository):
    def __init__(self, user: User | None = None) -> None:
        self.user = user
        self.updates = 0

    async def get_by_identity(self, issuer: str, subject: str) -> User | None:
        return self.user

    async def create(self, user: User) -> User:
        self.user = user
        return user

    async def update_profile(self, user: User) -> User:
        self.updates += 1
        self.user = user
        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.user


def _user(email: str | None = "old@example.com", name: str | None = "Old") -> User:
    return User(uuid4(), name, "https://identity.example", "sub", email, datetime.now(UTC))


@pytest.mark.anyio
async def test_new_user_stores_normalized_claims() -> None:
    service = UserService(_Repository())

    user = await service.get_or_create_user("iss", "sub", " Alice@Example.COM ", " Alice ")

    assert (user.email, user.display_name) == ("alice@example.com", "Alice")


@pytest.mark.anyio
async def test_existing_user_refreshes_changed_claims_and_keeps_identity() -> None:
    existing = _user()
    repository = _Repository(existing)

    user = await UserService(repository).get_or_create_user("iss", "sub", "New@Example.com", "New")

    assert (user.id, user.issuer, user.subject) == (
        existing.id,
        existing.issuer,
        existing.subject,
    )
    assert (user.email, user.display_name) == ("new@example.com", "New")
    assert repository.updates == 1


@pytest.mark.anyio
@pytest.mark.parametrize(("email", "name"), [(None, None), ("", "  "), ("OLD@example.com", "Old")])
async def test_existing_user_keeps_profile_when_claims_missing_or_unchanged(
    email: str | None, name: str | None
) -> None:
    existing = _user()
    repository = _Repository(existing)

    user = await UserService(repository).get_or_create_user("iss", "sub", email, name)

    assert user == existing
    assert repository.updates == 0
