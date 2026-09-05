from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import app.infrastructure.auth.oidc as oidc_verifier
from app.application.services.userService import UserService

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    token = credentials.credentials

    try:
        claims = oidc_verifier.verify(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    user = await UserService.get_or_create_user(
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims.get("email"),
    )

    return user