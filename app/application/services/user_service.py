from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from app.application.ports.user_repository import UserRepository
from app.application.services.team_invitation_service import normalize_email
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
        # Missing claims keep the stored value; present claims refresh it.
        email = normalize_email(email) if email and email.strip() else None
        display_name = display_name.strip() if display_name and display_name.strip() else None

        user = await self.repository.get_by_identity(
            issuer=issuer,
            subject=subject,
        )

        if user is not None:
            refreshed = replace(
                user,
                email=email or user.email,
                display_name=display_name or user.display_name,
            )
            if refreshed != user:
                return await self.repository.update_profile(refreshed)
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
