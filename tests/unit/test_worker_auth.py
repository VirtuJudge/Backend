from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from app.api.dependencies.worker_auth import (
    AuthenticatedWorker,
    get_worker_auth_verifier,
    require_worker_auth,
)
from app.infrastructure.auth.worker_auth import (
    WorkerAuthConfigurationError,
    WorkerAuthVerifier,
    create_worker_auth_verifier,
)
from app.settings import Settings


def test_worker_auth_verifier_verification() -> None:
    secret = SecretStr("top-secret-worker-token-xyz")
    verifier = WorkerAuthVerifier(secret)

    assert verifier.verify("top-secret-worker-token-xyz") is True
    assert verifier.verify("wrong-token") is False
    assert verifier.verify("") is False
    assert verifier.verify("top-secret-worker-token-xy") is False


def test_worker_auth_verifier_repr_does_not_leak_secret() -> None:
    secret = SecretStr("super-sensitive-secret-token")
    verifier = WorkerAuthVerifier(secret)

    rep = repr(verifier)
    assert "super-sensitive-secret-token" not in rep
    assert rep == "WorkerAuthVerifier(configured=True)"


def test_create_worker_auth_verifier_production_safety() -> None:
    # Production without secret must fail safely
    prod_missing_settings = Settings(
        app_env="production",
        ai_worker_shared_secret=None,
    )
    with pytest.raises(WorkerAuthConfigurationError) as exc_info:
        create_worker_auth_verifier(prod_missing_settings)
    assert "AI_WORKER_SHARED_SECRET must be configured in production" in str(exc_info.value)

    # Production with empty string secret must also fail safely
    prod_empty_settings = Settings(
        app_env="production",
        ai_worker_shared_secret=SecretStr("   "),
    )
    with pytest.raises(WorkerAuthConfigurationError):
        create_worker_auth_verifier(prod_empty_settings)

    # Production with valid secret succeeds
    prod_valid_settings = Settings(
        app_env="production",
        ai_worker_shared_secret=SecretStr("valid-production-secret-123"),
    )
    verifier = create_worker_auth_verifier(prod_valid_settings)
    assert verifier is not None
    assert verifier.verify("valid-production-secret-123") is True


def test_create_worker_auth_verifier_non_production_optional() -> None:
    dev_settings = Settings(
        app_env="development",
        ai_worker_shared_secret=None,
    )
    assert create_worker_auth_verifier(dev_settings) is None

    test_settings = Settings(
        app_env="test",
        ai_worker_shared_secret=None,
    )
    assert create_worker_auth_verifier(test_settings) is None

    test_with_secret = Settings(
        app_env="test",
        ai_worker_shared_secret=SecretStr("dev-secret"),
    )
    test_verifier = create_worker_auth_verifier(test_with_secret)
    assert test_verifier is not None
    assert test_verifier.verify("dev-secret") is True


@pytest.mark.asyncio
async def test_require_worker_auth_dependency_unit() -> None:
    verifier = WorkerAuthVerifier(SecretStr("correct-token"))

    # Correct credentials
    valid_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="correct-token")
    auth_worker = await require_worker_auth(credentials=valid_creds, verifier=verifier)
    assert isinstance(auth_worker, AuthenticatedWorker)
    assert auth_worker.role == "ai_worker"

    # Missing credentials
    with pytest.raises(HTTPException) as missing_exc:
        await require_worker_auth(credentials=None, verifier=verifier)
    assert missing_exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert missing_exc.value.headers == {"WWW-Authenticate": "Bearer"}
    assert missing_exc.value.detail == "Invalid authentication credentials"

    # Malformed: wrong scheme
    wrong_scheme = HTTPAuthorizationCredentials(scheme="Basic", credentials="correct-token")
    with pytest.raises(HTTPException) as scheme_exc:
        await require_worker_auth(credentials=wrong_scheme, verifier=verifier)
    assert scheme_exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert scheme_exc.value.headers == {"WWW-Authenticate": "Bearer"}

    # Malformed: empty credentials
    empty_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="")
    with pytest.raises(HTTPException) as empty_exc:
        await require_worker_auth(credentials=empty_creds, verifier=verifier)
    assert empty_exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert empty_exc.value.headers == {"WWW-Authenticate": "Bearer"}

    # Incorrect credentials
    wrong_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="incorrect-token")
    with pytest.raises(HTTPException) as wrong_exc:
        await require_worker_auth(credentials=wrong_creds, verifier=verifier)
    assert wrong_exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert wrong_exc.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_get_worker_auth_verifier_unconfigured() -> None:
    request = MagicMock()
    request.app.state.worker_auth_verifier = None

    with pytest.raises(HTTPException) as exc:
        await get_worker_auth_verifier(request)
    assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc.value.detail == "Worker authentication provider is not configured"
