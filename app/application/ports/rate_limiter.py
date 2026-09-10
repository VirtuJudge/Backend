from typing import Protocol


class RateLimitExceeded(Exception):
    pass


class RateLimiter(Protocol):
    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> None: ...
