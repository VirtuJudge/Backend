from dataclasses import dataclass
from typing import NoReturn

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.ports.worker_auth import WorkerAuthVerifier

security = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedWorker:
    role: str = "ai_worker"


def _raise_unauthorized() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_worker_auth_verifier(request: Request) -> WorkerAuthVerifier:
    verifier = getattr(request.app.state, "worker_auth_verifier", None)
    if not isinstance(verifier, WorkerAuthVerifier):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker authentication provider is not configured",
        )
    return verifier


async def require_worker_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    verifier: WorkerAuthVerifier = Depends(get_worker_auth_verifier),
) -> AuthenticatedWorker:
    if credentials is None or not credentials.credentials or credentials.scheme.lower() != "bearer":
        _raise_unauthorized()

    if not verifier.verify(credentials.credentials):
        _raise_unauthorized()

    return AuthenticatedWorker()


get_authenticated_worker = require_worker_auth
verify_worker_auth = require_worker_auth
