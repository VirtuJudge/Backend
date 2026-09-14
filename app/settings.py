from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
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
    celery_broker_url: str | None = None
    ai_worker_task_name: str = "app.worker.process_job"
    ai_worker_queue_name: str = "ai_jobs"
    object_storage_endpoint: str = "http://localhost:9000"

    object_storage_public_endpoint: str | None = None
    object_storage_bucket: str = "virtujudge"
    object_storage_region: str = "us-east-1"
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_upload_url_ttl_seconds: int = 900
    object_storage_download_url_ttl_seconds: int = 900
    asset_cleanup_enabled: bool | None = None
    asset_cleanup_interval_seconds: float = Field(default=300.0, ge=0.001)
    asset_cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    asset_cleanup_retention_seconds: int = Field(default=86400, ge=0)
    asset_cleanup_lease_seconds: int = Field(default=300, ge=1)
    asset_cleanup_tombstone_delay_seconds: int = Field(default=86400, ge=1)
    ai_job_dispatcher_enabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("ai_job_dispatcher_enabled", "ai_dispatcher_enabled"),
    )
    ai_job_dispatcher_interval_seconds: float = Field(
        default=10.0,
        ge=0.001,
        validation_alias=AliasChoices(
            "ai_job_dispatcher_interval_seconds",
            "ai_dispatcher_interval_seconds",
            "ai_dispatcher_scan_interval_seconds",
        ),
    )
    ai_job_dispatcher_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        validation_alias=AliasChoices(
            "ai_job_dispatcher_batch_size",
            "ai_dispatcher_batch_size",
        ),
    )
    ai_job_dispatcher_base_backoff_seconds: float = Field(
        default=30.0,
        ge=0.1,
        validation_alias=AliasChoices(
            "ai_job_dispatcher_base_backoff_seconds",
            "ai_dispatcher_base_backoff_seconds",
        ),
    )
    ai_job_dispatcher_max_backoff_seconds: float = Field(
        default=3600.0,
        ge=1.0,
        validation_alias=AliasChoices(
            "ai_job_dispatcher_max_backoff_seconds",
            "ai_dispatcher_max_backoff_seconds",
        ),
    )
    ai_job_dispatcher_backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        validation_alias=AliasChoices(
            "ai_job_dispatcher_backoff_factor",
            "ai_dispatcher_backoff_factor",
        ),
    )
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    ai_worker_shared_secret: SecretStr | None = None
    mail_backend: Literal["fake", "gmail", "resend"] = "fake"
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_smtp_username: str | None = None
    gmail_smtp_password: SecretStr | None = None
    gmail_from_address: str | None = None
    gmail_smtp_timeout_seconds: float = 10.0
    gmail_smoke_allowlist: str | None = None
    resend_api_url: str = "https://api.resend.com"
    resend_api_key: SecretStr | None = None
    resend_from_address: str | None = None
    resend_timeout_seconds: float = 10.0
    frontend_url: str | None = None
    cors_allowed_origins: str = ""

    def allowed_cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def effective_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def ai_dispatcher_enabled(self) -> bool | None:
        return self.ai_job_dispatcher_enabled

    @property
    def ai_dispatcher_interval_seconds(self) -> float:
        return self.ai_job_dispatcher_interval_seconds

    @property
    def ai_dispatcher_batch_size(self) -> int:
        return self.ai_job_dispatcher_batch_size

    @property
    def ai_dispatcher_base_backoff_seconds(self) -> float:
        return self.ai_job_dispatcher_base_backoff_seconds

    @property
    def ai_dispatcher_max_backoff_seconds(self) -> float:
        return self.ai_job_dispatcher_max_backoff_seconds

    @property
    def ai_dispatcher_backoff_factor(self) -> float:
        return self.ai_job_dispatcher_backoff_factor
