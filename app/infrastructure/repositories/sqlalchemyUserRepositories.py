from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.userRepository import UserRepository
from app.domain.user import User
from app.infrastructure.persistence.configurations.userConfigration import UserModel


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_identity(
        self,
        issuer: str,
        subject: str,
    ) -> User | None:

        stmt = select(UserModel).where(
            UserModel.issuer == issuer,
            UserModel.subject == subject,
        )

        row = await self.session.scalar(stmt)

        if row is None:
            return None

        return User(
            id=row.id,
            issuer=row.issuer,
            subject=row.subject,
            email=row.email,
            created_at=row.created_at,
        )

    async def create(self, user: User) -> User:

        stmt = insert(UserModel).values(
            id=user.id,
            issuer=user.issuer,
            subject=user.subject,
            email=user.email,
            display_name=user.display_name or "",
            created_at=user.created_at,
        )

        await self.session.execute(stmt)

        return user
