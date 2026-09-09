from typing import Any

import jwt
from jwt import PyJWKClient


class OIDCTokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: list[str] | None = None,
    ):
        self.issuer = issuer
        self.audience = audience
        self.jwks_client = PyJWKClient(jwks_url)
        self.algorithms = algorithms or ["RS256", "ES256"]

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)

        return jwt.decode(
            token,
            signing_key.key,
            algorithms=self.algorithms,
            issuer=self.issuer,
            audience=self.audience,
            options={
                "require": ["exp", "sub","name"],
            },
        )
