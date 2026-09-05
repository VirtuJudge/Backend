from typing import cast

from app.infrastructure.auth.oidc import OIDCTokenVerifier
from app.infrastructure.settings import Settings

settings = Settings()

logto_verifier = OIDCTokenVerifier(
    issuer=cast(str, settings.oidc_issuer),
    audience=cast(str, settings.oidc_audience),
    jwks_url=cast(str, settings.oidc_jwks_url),
)
