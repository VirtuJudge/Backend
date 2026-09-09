from app.infrastructure.auth.oidc import OIDCTokenVerifier
from app.settings import Settings


def create_token_verifier(settings: Settings) -> OIDCTokenVerifier | None:
    if settings.oidc_issuer is None:
        return None
    if settings.oidc_audience is None:
        return None
    if settings.oidc_jwks_url is None:
        return None
    return OIDCTokenVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.oidc_jwks_url,
    )
