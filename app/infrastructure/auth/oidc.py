from typing import Any

import jwt
from jwt import PyJWKClient


class OIDCTokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
    ):
        self.issuer = issuer
        self.audience = audience
        self.jwks_client = PyJWKClient(jwks_url)

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)

        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=self.issuer,
            audience=self.audience,
        )
