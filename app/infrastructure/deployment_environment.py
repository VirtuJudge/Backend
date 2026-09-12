"""Fail-fast checks for deployment configurations with known network incompatibilities."""

from __future__ import annotations

import sys

from sqlalchemy.engine import make_url

from app.settings import Settings


class DeploymentEnvironmentError(RuntimeError):
    """A deployment setting is valid syntactically but cannot work on the target network."""


def validate_deployment_environment(settings: Settings) -> None:
    if settings.app_env != "production":
        return

    database_host = (make_url(settings.database_url).host or "").lower()
    if database_host.startswith("db.") and database_host.endswith(".supabase.co"):
        raise DeploymentEnvironmentError(
            "DATABASE_URL uses Supabase's IPv6-only direct database endpoint. "
            "Render cannot reach this endpoint. In Supabase, open Connect, select "
            "Supabase Session pooler, copy its port 5432 connection string, and use "
            "that value for DATABASE_URL."
        )


def main() -> None:
    try:
        validate_deployment_environment(Settings())
    except DeploymentEnvironmentError as error:
        print(f"Deployment configuration error: {error}", file=sys.stderr)
        raise SystemExit(78) from None


if __name__ == "__main__":
    main()
