import asyncio
import os
from pathlib import Path
from uuid import UUID

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ai_job_contracts import AIWorkerUpdate
from app.infrastructure.persistence.configurations import PracticeSessionModel, ProjectModel
from tests.acceptance.test_erasure_workflow import Queue, due_again, seed, workflow
from tests.support.fakes import FakeObjectStorage


@pytest.mark.anyio
async def test_project_erasure_runs_real_ai_consumer_and_purges_all_stores(
    db_session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    ai_root = Path(__file__).resolve().parents[3] / "AI-ML"
    python = ai_root / ".venv/bin/python"
    if not python.is_file():
        pytest.skip("Cross-repository check requires the AI-ML test environment")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage, queue = FakeObjectStorage(), Queue()
    async with db_session_factory() as session:
        target, unrelated = await seed(session), await seed(session)
        for data in (target, unrelated):
            for key in (data["key"], data["pdf_key"]):
                await storage.put_object(key, b"synthetic", "application/octet-stream")
            await redis.set(f"session-events:{{{data['session']}}}:sequence", "1")
        work = workflow(session, storage, queue, redis)
        request = await work.request(
            "project", target["project"], target["owner"], "cross-repo", "Synthetic project"
        )
        await work.run_due()
        message = queue.messages[0]
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-m",
            "tests.erasure_bridge",
            str(tmp_path / "ai-objects"),
            cwd=ai_root,
            env=os.environ.copy(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(message.model_dump_json(exclude_none=True).encode()), timeout=30
        )
        assert process.returncode == 0, stderr.decode()
        update = AIWorkerUpdate.model_validate_json(stdout)
        assert update.status.value == "completed"
        assert update.payload["deleted_records"] == 1
        assert update.payload["deleted_objects"] == 4
        await work.record_update(UUID(message.job_id), update)
        await due_again(session, request.id)
        await work.run_due()
        assert (await work.get(request.id, target["owner"])).status == "completed"
        assert await session.get(ProjectModel, target["project"], populate_existing=True) is None
        assert (
            await session.get(PracticeSessionModel, target["session"], populate_existing=True)
            is None
        )
        assert target["key"] not in storage.objects and target["pdf_key"] not in storage.objects
        assert unrelated["key"] in storage.objects and unrelated["pdf_key"] in storage.objects
        assert await redis.get(f"session-events:{{{target['session']}}}:sequence") is None
        assert await redis.get(f"session-events:{{{unrelated['session']}}}:sequence") == "1"
    await redis.aclose()
