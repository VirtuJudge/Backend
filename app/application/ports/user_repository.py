from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_identity(
        self,
        issuer: str,
        subject: str,
    ) -> User | None: ...

    @abstractmethod
    async def create(
        self,
        user: User,
    ) -> User: ...

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None: ...
