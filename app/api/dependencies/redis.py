# app/api/dependencies/redis.py

from fastapi import Request

from app.application.interfaces.rate_limiter import RateLimiter


def get_redis(request: Request) -> RateLimiter:
    return request.app.state.redis
