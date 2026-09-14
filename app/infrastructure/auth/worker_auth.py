import hmac

from pydantic import SecretStr

from app.settings import Settings


class WorkerAuthConfigurationError(RuntimeError):
    """Raised when worker authentication configuration is missing or invalid."""


class WorkerAuthVerifier:
    def __init__(self, shared_secret: SecretStr | str) -> None:
        if isinstance(shared_secret, SecretStr):
            self._secret: str = shared_secret.get_secret_value()
        else:
            self._secret = str(shared_secret)

    def verify(self, supplied_credential: str) -> bool:
        if not supplied_credential:
            return False
        return hmac.compare_digest(supplied_credential, self._secret)

    def __repr__(self) -> str:
        return "WorkerAuthVerifier(configured=True)"


def create_worker_auth_verifier(settings: Settings) -> WorkerAuthVerifier | None:
    if (
        settings.ai_worker_shared_secret is None
        or not settings.ai_worker_shared_secret.get_secret_value().strip()
    ):
        if settings.app_env == "production":
            raise WorkerAuthConfigurationError(
                "AI_WORKER_SHARED_SECRET must be configured in production."
            )
        return None
    return WorkerAuthVerifier(settings.ai_worker_shared_secret)
