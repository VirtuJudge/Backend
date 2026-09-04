from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from local_stack.config import (
    BLPOP_TIMEOUT_SECONDS,
    DEFAULT_REDIS_URL,
    HEARTBEAT_KEY,
    HEARTBEAT_TTL_SECONDS,
    QUEUE_KEY,
    RESULT_KEY_PREFIX,
    RESULT_TTL_SECONDS,
)
from local_stack.loader import AIComponents, load_ai_components

logger = logging.getLogger(__name__)


class LocalStackWorker:
    def __init__(self, redis_client: aioredis.Redis, components: AIComponents) -> None:
        self.redis = redis_client
        self.components = components
        self.pipeline = components.fake_pipeline_cls()

    async def process_raw_message(self, raw_data: str | bytes) -> str | None:
        try:
            message = self.components.queue_message_cls.model_validate_json(raw_data)
        except ValueError:
            logger.warning("Invalid diagnostic job discarded")
            return None

        try:
            update = await self.components.process_job(message, self.pipeline)
            result: dict[str, Any] = update.model_dump(mode="json")
            if result["status"] == "failed":
                result["payload"] = {"message": "Diagnostic analysis failed"}
            serialized = json.dumps(result)
        except Exception:
            logger.error("Diagnostic job processing failed")
            return None

        await self.redis.set(
            f"{RESULT_KEY_PREFIX}{message.job_id}", serialized, ex=RESULT_TTL_SECONDS
        )
        return str(message.job_id)

    async def run_once(self) -> str | None:
        await self.redis.set(HEARTBEAT_KEY, str(time.time()), ex=HEARTBEAT_TTL_SECONDS)
        item = await self.redis.blpop([QUEUE_KEY], timeout=BLPOP_TIMEOUT_SECONDS)
        if item is None:
            return None
        return await self.process_raw_message(item[1])

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.run_once()
            except RedisError:
                logger.warning("Diagnostic queue unavailable")
                await asyncio.sleep(1)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = aioredis.Redis.from_url(
        os.getenv("REDIS_URL", DEFAULT_REDIS_URL),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=3,
    )
    worker = LocalStackWorker(client, load_ai_components())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await worker.run(stop)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
