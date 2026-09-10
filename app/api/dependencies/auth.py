from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies.services import get_user_service
from app.application.services.user_service import UserService
from app.domain.user import User

security = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user_service: UserService = Depends(get_user_service),
) -> User:
    token = credentials.credentials
    verifier = request.app.state.token_verifier
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication provider is not configured",
        )

    try:
        claims = verifier.verify(token)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from error

    user = await user_service.get_or_create_user(
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims.get("email"),
        display_name=claims.get("display_name"),
    )

    return user
