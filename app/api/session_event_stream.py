import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import Request

from app.application.ports.session_notification import SessionNotificationPort
from app.application.session_notification_contracts import (
    PracticeSessionResyncRequiredNotification,
    ResyncReason,
)


async def stream_live_session_events(
    request: Request,
    notifications: SessionNotificationPort,
    practice_session_id: str,
    after_sequence: int | None,
    *,
    heartbeat_seconds: float = 15.0,
) -> AsyncGenerator[str, None]:
    requested_sequence = after_sequence
    initial = await notifications.replay(practice_session_id, after_sequence or 0)
    cursor = initial.current_sequence
    resync_reason: ResyncReason | None = None
    if after_sequence is None and initial.current_sequence > 0:
        resync_reason = ResyncReason.CURSOR_MISSING
    elif initial.gap_reason is not None:
        resync_reason = ResyncReason(initial.gap_reason.value)
    if resync_reason is not None:
        yield PracticeSessionResyncRequiredNotification(
            sequence=initial.current_sequence,
            practice_session_id=practice_session_id,
            reason=resync_reason,
            current_sequence=initial.current_sequence,
            requested_sequence=requested_sequence,
            occurred_at=datetime.now(UTC),
            trace_id=getattr(request.state, "correlation_id", "unknown"),
        ).to_sse_frame()
    else:
        for event in initial.events:
            cursor = event.sequence
            yield event.to_sse_frame()
    try:
        while not await request.is_disconnected():
            batch = await notifications.wait_for_events(
                practice_session_id,
                cursor,
                timeout_seconds=heartbeat_seconds,
            )
            if await request.is_disconnected():
                break
            if batch.gap_reason is not None:
                cursor = batch.current_sequence
                yield PracticeSessionResyncRequiredNotification(
                    sequence=cursor,
                    practice_session_id=practice_session_id,
                    reason=ResyncReason(batch.gap_reason.value),
                    current_sequence=cursor,
                    requested_sequence=requested_sequence,
                    occurred_at=datetime.now(UTC),
                    trace_id=getattr(request.state, "correlation_id", "unknown"),
                ).to_sse_frame()
                continue
            if not batch.events:
                yield ": heartbeat\n\n"
                continue
            for event in batch.events:
                cursor = event.sequence
                yield event.to_sse_frame()
    except asyncio.CancelledError:
        raise
