import asyncio
from collections.abc import AsyncGenerator

from fastapi import Request

from app.application.ports.session_notification import SessionNotificationPort


async def stream_live_session_events(
    request: Request,
    notifications: SessionNotificationPort,
    practice_session_id: str,
    after_sequence: int,
    *,
    heartbeat_seconds: float = 15.0,
) -> AsyncGenerator[str, None]:
    cursor = after_sequence
    try:
        while not await request.is_disconnected():
            batch = await notifications.wait_for_events(
                practice_session_id,
                cursor,
                timeout_seconds=heartbeat_seconds,
            )
            if await request.is_disconnected():
                break
            if not batch.events:
                yield ": heartbeat\n\n"
                continue
            for event in batch.events:
                cursor = event.sequence
                yield event.to_sse_frame()
    except asyncio.CancelledError:
        raise
