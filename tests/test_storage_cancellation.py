import asyncio
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from app.infrastructure.settings import Settings
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage


@pytest.mark.anyio
async def test_cancelled_transfer_waits_for_writer_before_caller_unlinks(tmp_path: Path) -> None:
    entered, release, closed = Event(), Event(), Event()

    class Body(BytesIO):
        def close(self) -> None:
            super().close()
            closed.set()

    def get_object(**kwargs: object) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return {"Body": Body(b"synthetic private document"), "ContentType": "application/pdf"}

    storage = S3ObjectStorage(Settings(_env_file=None))
    storage._internal_client = Mock(get_object=get_object)
    target = tmp_path / "upload.pdf"
    task = asyncio.create_task(storage.stream_to_disk("synthetic-key", target, 1024))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    try:
        await asyncio.sleep(0.02)
        assert not task.done(), "Cancellation must wait until the storage writer has stopped"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 2)
        target.unlink(missing_ok=True)
    assert not target.exists()
