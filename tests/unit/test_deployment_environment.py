import pytest

from app.infrastructure.deployment_environment import (
    DeploymentEnvironmentError,
    validate_deployment_environment,
)
from app.settings import Settings


def test_production_rejects_ipv6_only_supabase_direct_database_url() -> None:
    settings = Settings(
        app_env="production",
        database_url=(
            "postgresql+asyncpg://postgres:private-password@"
            "db.project-ref.supabase.co:5432/postgres"
        ),
    )

    with pytest.raises(DeploymentEnvironmentError) as exc_info:
        validate_deployment_environment(settings)

    message = str(exc_info.value)
    assert "Supabase Session pooler" in message
    assert "private-password" not in message


def test_production_accepts_supabase_session_pooler_database_url() -> None:
    settings = Settings(
        app_env="production",
        database_url=(
            "postgresql+asyncpg://postgres.project-ref:private-password@"
            "aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
        ),
    )

    validate_deployment_environment(settings)
