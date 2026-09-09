# app/infrastructure/redis/rate_limiter.py

from redis.asyncio import Redis

from app.application.interfaces.rate_limiter import RateLimiter ,RateLimitExceeded




class RedisRateLimiter(RateLimiter):

    def __init__(self, redis: Redis):
        self.redis = redis

    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> None:

        count = await self.redis.incr(key)

        if count == 1:
            await self.redis.expire(
                key,
                window_seconds,
            )

        if count > limit:
            raise RateLimitExceeded