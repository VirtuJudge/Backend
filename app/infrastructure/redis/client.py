# app/infrastructure/redis/client.py

from redis.asyncio import Redis

from app.settings import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
