from app.infrastructure.auth.oidc import OIDCTokenVerifier
from app.infrastructure.settings import Settings

settings = Settings()

logto_verifier = OIDCTokenVerifier(
    issuer=settings.oidc_issuer,
    audience=settings.oidc_audience,
    jwks_url=settings.oidc_jwks_url,
)
