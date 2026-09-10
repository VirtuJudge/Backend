from typing import cast

from fastapi import Request

from app.application.ports.rate_limiter import RateLimiter


def get_redis(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.redis)
