from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import app.infrastructure.auth.provider as logto_verifier
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_session
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository
from app.application.services.userService import UserService
from app.domain.user import User

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = credentials.credentials

    try:
        claims = logto_verifier.verify(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    user = await UserService(SqlAlchemyUserRepository(session)).get_or_create_user(
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims.get("email"),
    )

    return user