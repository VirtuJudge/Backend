from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.redis import get_redis
from app.application.ports.rate_limiter import RateLimiter, RateLimitExceeded
from app.domain.user import User


def rate_limit(
    name: str,
    limit: int,
    window_seconds: int,
) -> Callable[..., Awaitable[None]]:
    async def dependency(
        current_user: User = Depends(get_current_user),
        redis: RateLimiter = Depends(get_redis),
    ) -> None:

        key = f"rate_limit:{name}:user:{current_user.id}"

        try:
            await redis.check(
                key=key,
                limit=limit,
                window_seconds=window_seconds,
            )
        except RateLimitExceeded as error:
            raise HTTPException(
                status_code=429,
                detail="rate_limit_exceeded",
            ) from error

    return dependency
