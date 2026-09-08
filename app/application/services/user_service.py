from datetime import UTC, datetime
from uuid import uuid4

from app.application.ports.user_repository import UserRepository
from app.domain.user import User


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def get_or_create_user(
        self,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str | None,
    ) -> User:
        user = await self.repository.get_by_identity(
            issuer=issuer,
            subject=subject,
        )

        if user is not None:
            return user

        user = User(
            id=uuid4(),
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            created_at=datetime.now(UTC),
        )

        return await self.repository.create(user)
