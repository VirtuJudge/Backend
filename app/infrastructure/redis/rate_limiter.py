# app/infrastructure/redis/rate_limiter.py

from redis.asyncio import Redis

from app.application.interfaces.rate_limiter import RateLimiter, RateLimitExceeded


class RedisRateLimiter(RateLimiter):
    def __init__(self, redis: Redis):
        self.redis = redis

    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> None:

        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, window_seconds, nx=True)
            count, _ = await pipeline.execute()

        if count > limit:
            raise RateLimitExceeded
