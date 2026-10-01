from redis.asyncio import Redis


class RedisErasureCache:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def delete_sessions(self, session_ids: list[str]) -> int:
        count = 0
        for session_id in session_ids:
            tag = f"{{{session_id}}}"
            count += int(
                await self.redis.delete(
                    f"session-events:{tag}:sequence",
                    f"session-events:{tag}:stream",
                )
            )
        return count
