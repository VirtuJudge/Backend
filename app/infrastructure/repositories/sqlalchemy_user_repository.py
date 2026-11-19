from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.user_repository import UserRepository
from app.domain.user import User
from app.infrastructure.persistence.configurations.user_configuration import UserModel


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

        model = await self.session.scalar(stmt)

        if model is None:
            return None

        return User(
            id=model.id,
            issuer=model.issuer,
            subject=model.subject,
            email=model.email,
            display_name=model.display_name,
            created_at=model.created_at,
        )

    async def create(self, user: User) -> User:
        insert = sqlite_insert if self.session.get_bind().dialect.name == "sqlite" else pg_insert
        result = await self.session.execute(
            insert(UserModel)
            .values(
                id=user.id,
                issuer=user.issuer,
                subject=user.subject,
                email=user.email,
                display_name=user.display_name,
                created_at=user.created_at,
            )
            .on_conflict_do_nothing(index_elements=["issuer", "subject"])
            .returning(UserModel.id)
        )
        if result.scalar_one_or_none() is not None:
            return user
        # A concurrent request created this identity first.
        existing = await self.get_by_identity(user.issuer, user.subject)
        assert existing is not None
        return existing

    async def update_profile(self, user: User) -> User:
        stmt = (
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(email=user.email, display_name=user.display_name)
        )

        await self.session.execute(stmt)

        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        stmt = select(UserModel).where(
            UserModel.id == user_id,
        )

        model = await self.session.scalar(stmt)
        if model is None:
            return None

        return User(
            id=model.id,
            display_name=model.display_name,
            issuer=model.issuer,
            subject=model.subject,
            email=model.email,
            created_at=model.created_at,
        )
