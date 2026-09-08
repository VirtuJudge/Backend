from functools import lru_cache

from app.infrastructure.settings import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings()