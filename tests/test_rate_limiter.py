import fakeredis.aioredis
import pytest

from app.application.interfaces.rate_limiter import RateLimitExceeded
from app.infrastructure.redis.rate_limiter import (
    RedisRateLimiter,
)


@pytest.mark.asyncio
async def test_allows_requests_within_limit() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RedisRateLimiter(redis)

    for _ in range(3):
        await limiter.check(
            key="rate_limit:user:1",
            limit=3,
            window_seconds=60,
        )


@pytest.mark.asyncio
async def test_rejects_requests_when_limit_is_exceeded() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RedisRateLimiter(redis)

    for _ in range(3):
        await limiter.check(
            key="rate_limit:user:1",
            limit=3,
            window_seconds=60,
        )

    with pytest.raises(RateLimitExceeded):
        await limiter.check(
            key="rate_limit:user:1",
            limit=3,
            window_seconds=60,
        )


@pytest.mark.asyncio
async def test_sets_expiration() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RedisRateLimiter(redis)

    await limiter.check(
        key="rate_limit:user:1",
        limit=10,
        window_seconds=60,
    )

    ttl = await redis.ttl("rate_limit:user:1")

    assert 0 < ttl <= 60


@pytest.mark.asyncio
async def test_different_users_have_separate_limits() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RedisRateLimiter(redis)

    for _ in range(3):
        await limiter.check(
            key="rate_limit:user:1",
            limit=3,
            window_seconds=60,
        )

    # User 2 has a separate rate-limit counter.
    await limiter.check(
        key="rate_limit:user:2",
        limit=3,
        window_seconds=60,
    )
