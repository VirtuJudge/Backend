from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import app.infrastructure.auth.provider as logto_verifier
from app.application.services.userService import UserService
from app.domain.user import User
from app.infrastructure.database import get_session
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = credentials.credentials

    try:
        claims = logto_verifier.verify(token)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from error

    user = await UserService(SqlAlchemyUserRepository(session)).get_or_create_user(
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims.get("email"),
    )

    return user
