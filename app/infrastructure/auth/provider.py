from app.infrastructure.settings import Settings
from app.infrastructure.auth.oidc import OIDCTokenVerifier

settings = Settings()

logto_verifier = OIDCTokenVerifier(
    issuer=settings.oidc_issuer,
    audience=settings.oidc_audience,
    jwks_url=settings.oidc_jwks_url,
)
