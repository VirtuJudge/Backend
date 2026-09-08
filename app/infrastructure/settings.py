from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "VirtuJudge Backend"
    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = (
        "postgresql+asyncpg://virtujudge_backend:virtujudge_backend@localhost:5432/virtujudge"
    )
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_url: str = "redis://localhost:6379/1"
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "virtujudge"
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    ai_worker_shared_secret: SecretStr | None = None
    mail_backend: Literal["fake", "gmail"] = "fake"
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_smtp_username: str | None = None
    gmail_smtp_password: SecretStr | None = None
    gmail_from_address: str | None = None
    gmail_smtp_timeout_seconds: float = 10.0
    gmail_smoke_allowlist: str | None = None
    frontend_url: str | None = None
