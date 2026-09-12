import pytest

from app.domain.user import User
from tests.support.constants import MEMBER_ID, NOW, OUTSIDER_ID
from tests.support.database import db_session_factory, seed_db

__all__ = ["db_session_factory", "seed_db", "member_user", "outsider_user"]


@pytest.fixture
def member_user() -> User:
    return User(
        id=MEMBER_ID,
        display_name="Member User",
        issuer="https://auth.example",
        subject="member-sub",
        email="member@example.com",
        created_at=NOW,
    )


@pytest.fixture
def outsider_user() -> User:
    return User(
        id=OUTSIDER_ID,
        display_name="Outsider User",
        issuer="https://auth.example",
        subject="outsider-sub",
        email="outsider@example.com",
        created_at=NOW,
    )
