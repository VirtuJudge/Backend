import os
import sys
import time

from redis import Redis
from redis.exceptions import RedisError

from local_stack.config import DEFAULT_REDIS_URL, HEARTBEAT_KEY, HEARTBEAT_TTL_SECONDS


def check_worker_health() -> bool:
    try:
        with Redis.from_url(
            os.getenv("REDIS_URL", DEFAULT_REDIS_URL),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        ) as client:
            heartbeat = client.get(HEARTBEAT_KEY)
            return (
                heartbeat is not None
                and 0 <= time.time() - float(heartbeat) < HEARTBEAT_TTL_SECONDS
            )
    except (RedisError, ValueError):
        return False


def main() -> int:
    if check_worker_health():
        return 0
    print("Worker heartbeat missing or stale", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
