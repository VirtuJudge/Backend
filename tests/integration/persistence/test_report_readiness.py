import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ai_job_contracts import (
    AnswerAnalysisCompletedPayload,
    FollowUpQuestion,
)
from app.application.ai_jobs import AIJobs
from app.application.ports.ai_queue import AIJobQueueAccepted, AIJobQueueEnvelope
from app.application.session_workflow import SessionWorkflow
from app.domain.asset import StorageUnavailable
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.persistence.configurations import ProjectModel
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    AnswerModel,
    PracticeSessionModel,
    QARoundModel,
    QuestionModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from tests.integration.persistence.test_report_repository import _seed_session_ancestry
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fakes import FakeObjectStorage
from tests.unit.test_ai_jobs_record_update import _make_update


@dataclass
class RoundData:
    session_id: UUID
    project_id: UUID
    attempt_id: UUID
    round_id: UUID
    actor_id: UUID
    final_question_id: UUID
    answers: list[UUID]
    jobs: list[UUID]
    qa_key: str


async def _seed_round(
    sessions: async_sessionmaker[AsyncSession], *, submitted: int = 2
) -> RoundData:
    async with sessions() as db:
        session, attempt, round_ = await _seed_session_ancestry(db)
        session.status = SessionStatus.QUESTIONS_IN_PROGRESS
        attempt.status = AnalysisAttemptStatus.RUNNING
        round_.state = QARoundState.IN_PROGRESS
        now = datetime.now(UTC)
        questions = [
            QuestionModel(
                id=uuid4(),
                qa_round_id=round_.id,
                practice_session_id=session.id,
                kind=QuestionKind.PRIMARY,
                position=index + 1,
                text=f"Question {index + 1}?",
                reason="Grounded",
                rubric_dimension="business_reasoning",
                evidence_ids=["ev-1"],
                state=QuestionState.ANSWERED if index < submitted else QuestionState.ACTIVE,
                created_at=now,
            )
            for index in range(submitted + 1)
        ]
        db.add_all(questions)
        await db.flush()
        round_.current_question_id = questions[-1].id
        manifest = await db.get(SessionManifestModel, attempt.manifest_id)
        assert manifest is not None
        answers = [
            AnswerModel(
                id=uuid4(),
                qa_round_id=round_.id,
                question_id=question.id,
                answered_by=session.created_by,
                status=AnswerStatus.SUBMITTED,
                audio_asset_version_id=manifest.presentation_version_id,
                duration_ms=1000,
                submitted_at=now - timedelta(minutes=1) + timedelta(seconds=index),
                created_at=now,
                updated_at=now,
            )
            for index, question in enumerate(questions[:-1])
        ]
        db.add_all(answers)
        await db.flush()
        jobs = [
            AnalysisJobModel(
                id=uuid4(),
                practice_session_id=session.id,
                attempt_id=attempt.id,
                analysis_attempt=1,
                answer_id=answer.id,
                job_type="analyze_answer",
                status=AnalysisJobStatus.RUNNING,
                correlation_id=uuid4(),
                created_at=now,
                updated_at=now,
                last_update_sequence=1,
                payload={"trace_id": "trc_test_update"},
            )
            for answer in answers
        ]
        db.add_all(jobs)
        db.add(
            AnalysisJobModel(
                id=uuid4(),
                practice_session_id=session.id,
                attempt_id=attempt.id,
                analysis_attempt=1,
                job_type="analyze_session",
                status=AnalysisJobStatus.COMPLETED,
                correlation_id=uuid4(),
                created_at=now,
                updated_at=now,
                completed_result={
                    "analysis_artifact": {
                        "artifact_id": "analysis-1",
                        "object_key": (
                            f"projects/{session.project_id}/sessions/{session.id}/"
                            "attempts/1/analysis.json"
                        ),
                        "checksum": "sha256:" + "b" * 64,
                        "schema_version": 1,
                    }
                },
            )
        )
        await db.commit()
        return RoundData(
            session.id,
            session.project_id,
            attempt.id,
            round_.id,
            session.created_by,
            questions[-1].id,
            [a.id for a in answers],
            [j.id for j in jobs],
            f"projects/{session.project_id}/sessions/{session.id}/attempts/1/qa.json",
        )


def _store_evidence(storage: FakeObjectStorage, data: RoundData) -> None:
    for answer_id in data.answers:
        prefix = f"ai/session/{data.session_id}/answers/{answer_id}"
        storage.objects[f"{prefix}/transcript.json"] = json.dumps(
            {"transcript": {"text": f"Answer {answer_id}"}}
        ).encode()
        storage.objects[f"{prefix}/assessment.json"] = json.dumps(
            {"assessment": {"text": "Assessment", "evidence_ids": ["ev-1"]}}
        ).encode()


async def _skip(
    sessions: async_sessionmaker[AsyncSession],
    data: RoundData,
    storage: FakeObjectStorage,
    queue: FakeAIJobQueue,
) -> UUID:
    async with sessions() as db:
        uow = SqlAlchemyUnitOfWork(db)
        workflow = SessionWorkflow(uow, ai_jobs=AIJobs(uow, storage=storage, queue=queue))
        answer = await workflow.skip_answer(
            question_id=data.final_question_id,
            actor_id=data.actor_id,
            reason="Need research",
            idempotency_key="final-skip",
        )
        return answer.id


async def _complete(
    sessions: async_sessionmaker[AsyncSession],
    data: RoundData,
    index: int,
    storage: FakeObjectStorage,
    queue: FakeAIJobQueue,
    *,
    follow_up: bool = True,
) -> None:
    async with sessions() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).record_update(
            data.jobs[index],
            _make_update(
                "completed",
                sequence=2,
                payload=AnswerAnalysisCompletedPayload(
                    answer_id=str(data.answers[index]),
                    transcript_artifact_id=f"transcript-{index}",
                    assessment_artifact_id=f"assessment-{index}",
                    follow_up=FollowUpQuestion(
                        text="Delayed follow-up?",
                        reason="Too late",
                        rubric_dimension="business_reasoning",
                        evidence_ids=["ev-1"],
                    )
                    if follow_up
                    else None,
                ),
            ),
        )


@pytest.mark.anyio
async def test_final_skip_waits_for_every_answer_and_older_last_callback_schedules_report(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await _seed_round(db_session_factory)
    storage, queue = FakeObjectStorage(), FakeAIJobQueue()
    skipped_id = await _skip(db_session_factory, data, storage, queue)
    assert queue.count == 0
    async with db_session_factory() as db:
        saved = await db.get(AnswerModel, skipped_id)
        session = await db.get(PracticeSessionModel, data.session_id)
        assert saved is not None and saved.status is AnswerStatus.SKIPPED
        assert session is not None and session.status is SessionStatus.REPORT_GENERATING
    _store_evidence(storage, data)
    await _complete(db_session_factory, data, 1, storage, queue)
    assert queue.count == 0
    assert data.qa_key not in storage.objects
    await _complete(db_session_factory, data, 0, storage, queue)
    assert queue.count == 1
    artifact = json.loads(storage.objects[data.qa_key])
    assert {a["transcript_artifact_id"] for a in artifact["answers"]} == {
        None,
        "transcript-0",
        "transcript-1",
    }
    async with db_session_factory() as db:
        round_ = await db.get(QARoundModel, data.round_id)
        assert round_ is not None and round_.state is QARoundState.COMPLETED
        assert round_.follow_up_count == 0
    await _complete(db_session_factory, data, 0, storage, queue)
    assert await _skip(db_session_factory, data, storage, queue) == skipped_id
    assert queue.count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["get", "put"])
@pytest.mark.parametrize("callback_first", [False, True])
async def test_storage_outage_preserves_skip_and_callback_then_fresh_dispatcher_recovers(
    db_session_factory: async_sessionmaker[AsyncSession],
    operation: str,
    callback_first: bool,
) -> None:
    data = await _seed_round(db_session_factory, submitted=1)

    class Storage(FakeObjectStorage):
        fail = True

        async def get_object(self, storage_key: str, max_bytes: int) -> bytes:
            if self.fail and operation == "get":
                raise StorageUnavailable("temporary read outage")
            return await super().get_object(storage_key, max_bytes)

        async def put_object(self, storage_key: str, data: bytes, content_type: str) -> None:
            if self.fail and operation == "put":
                raise StorageUnavailable("temporary write outage")
            await super().put_object(storage_key, data, content_type)

    storage, queue = Storage(), FakeAIJobQueue()
    _store_evidence(storage, data)
    if callback_first:
        await _complete(db_session_factory, data, 0, storage, queue, follow_up=False)
    skipped_id = await _skip(db_session_factory, data, storage, queue)
    if not callback_first:
        await _complete(db_session_factory, data, 0, storage, queue)
    assert queue.count == 0
    assert await _skip(db_session_factory, data, storage, queue) == skipped_id
    async with db_session_factory() as db:
        answer = await db.get(AnswerModel, data.answers[0])
        job = await db.get(AnalysisJobModel, data.jobs[0])
        assert answer is not None and answer.transcript_artifact_id == "transcript-0"
        assert job is not None and job.status is AnalysisJobStatus.COMPLETED
        assert job.last_update_sequence == 2
    storage.fail = False
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending()
    assert queue.count == 1
    assert data.qa_key in storage.objects
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending()
    assert queue.count == 1


@pytest.mark.anyio
async def test_simultaneous_preparation_creates_one_durable_report_job(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if db_session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking test")
    data = await _seed_round(db_session_factory, submitted=0)
    unavailable = FakeObjectStorage()
    unavailable.transient_failure = True
    await _skip(db_session_factory, data, unavailable, FakeAIJobQueue())
    writing, release, contender = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Storage(FakeObjectStorage):
        writes = 0

        async def put_object(self, storage_key: str, data: bytes, content_type: str) -> None:
            self.writes += 1
            writing.set()
            await release.wait()
            await super().put_object(storage_key, data, content_type)

    storage = Storage()
    async with db_session_factory() as first, db_session_factory() as second:
        first_uow, second_uow = SqlAlchemyUnitOfWork(first), SqlAlchemyUnitOfWork(second)
        original = second_uow.sessions.get_by_id_for_update

        async def competing_lock(session_id: UUID) -> PracticeSession | None:
            contender.set()
            return await original(session_id)

        monkeypatch.setattr(second_uow.sessions, "get_by_id_for_update", competing_lock)
        first_task = asyncio.create_task(
            AIJobs(first_uow, storage=storage).resume_report(data.session_id, dispatch=False)
        )
        await asyncio.wait_for(writing.wait(), timeout=5)
        second_task = asyncio.create_task(
            AIJobs(second_uow, storage=storage).resume_report(data.session_id, dispatch=False)
        )
        await asyncio.wait_for(contender.wait(), timeout=5)
        release.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
    assert storage.writes == 1
    async with db_session_factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AnalysisJobModel)
                .where(
                    AnalysisJobModel.attempt_id == data.attempt_id,
                    AnalysisJobModel.job_type == "generate_report",
                )
            )
            == 1
        )
    queue = FakeAIJobQueue()
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending()
    assert queue.count == 1
    async with db_session_factory() as db:
        uow = SqlAlchemyUnitOfWork(db)
        report_job = await uow.jobs.get_report_by_attempt_id(data.attempt_id)
        assert report_job is not None
        with pytest.raises(IdempotencyConflict):
            await uow.jobs.create(replace(report_job, id=uuid4()))
        assert await uow.jobs.get_report_by_attempt_id(data.attempt_id) == report_job


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ineligible",
    [
        "cancelled-session",
        "failed-session",
        "cancelled-attempt",
        "failed-attempt",
        "superseded",
        "revoked-session",
        "revoked-project",
        "missing-analysis",
        "failed-analysis",
        "cancelled-analysis",
        "missing-analysis-artifact",
    ],
)
async def test_recovery_and_direct_readiness_reject_ineligible_work(
    db_session_factory: async_sessionmaker[AsyncSession],
    ineligible: str,
) -> None:
    data = await _seed_round(db_session_factory, submitted=0)
    storage, queue = FakeObjectStorage(), FakeAIJobQueue()
    storage.transient_failure = True
    await _skip(db_session_factory, data, storage, queue)
    async with db_session_factory() as db:
        session = await db.get(PracticeSessionModel, data.session_id)
        attempt = await db.get(AnalysisAttemptModel, data.attempt_id)
        project = await db.get(ProjectModel, data.project_id)
        analysis = await db.scalar(
            select(AnalysisJobModel).where(
                AnalysisJobModel.attempt_id == data.attempt_id,
                AnalysisJobModel.job_type == "analyze_session",
            )
        )
        assert session is not None and attempt is not None and project is not None
        assert analysis is not None
        if ineligible.endswith("session"):
            if ineligible.startswith("revoked"):
                session.access_revoked_at = datetime.now(UTC)
            else:
                session.status = (
                    SessionStatus.CANCELLED
                    if ineligible.startswith("cancelled")
                    else SessionStatus.FAILED
                )
        elif ineligible.endswith("attempt"):
            attempt.status = (
                AnalysisAttemptStatus.CANCELLED
                if ineligible.startswith("cancelled")
                else AnalysisAttemptStatus.FAILED
            )
        elif ineligible == "revoked-project":
            project.access_revoked_at = datetime.now(UTC)
        elif ineligible == "superseded":
            db.add(
                AnalysisAttemptModel(
                    id=uuid4(),
                    session_id=data.session_id,
                    manifest_id=attempt.manifest_id,
                    attempt_number=2,
                    status=AnalysisAttemptStatus.RUNNING,
                    created_at=datetime.now(UTC),
                )
            )
        elif ineligible == "missing-analysis":
            await db.delete(analysis)
        elif ineligible == "missing-analysis-artifact":
            analysis.completed_result = {}
        else:
            analysis.status = (
                AnalysisJobStatus.FAILED
                if ineligible == "failed-analysis"
                else AnalysisJobStatus.CANCELLED
            )
        await db.commit()
    storage.transient_failure = False
    async with db_session_factory() as db:
        service = AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue)
        await service.redispatch_pending()
        await service.resume_report(data.session_id)
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AnalysisJobModel)
                .where(
                    AnalysisJobModel.job_type == "generate_report",
                )
            )
            == 0
        )
    assert queue.count == 0
    assert data.qa_key not in storage.objects


@pytest.mark.anyio
@pytest.mark.parametrize("waiting_for", ["answer", "analysis-artifact"])
async def test_dispatcher_batch_filters_rounds_still_waiting_for_analysis(
    db_session_factory: async_sessionmaker[AsyncSession],
    waiting_for: str,
) -> None:
    waiting = await _seed_round(db_session_factory, submitted=1 if waiting_for == "answer" else 0)
    ready = await _seed_round(db_session_factory, submitted=0)
    storage, queue = FakeObjectStorage(), FakeAIJobQueue()
    storage.transient_failure = True
    await _skip(db_session_factory, waiting, storage, queue)
    await _skip(db_session_factory, ready, storage, queue)
    if waiting_for == "analysis-artifact":
        async with db_session_factory() as db:
            job = await db.scalar(
                select(AnalysisJobModel).where(
                    AnalysisJobModel.attempt_id == waiting.attempt_id,
                    AnalysisJobModel.job_type == "analyze_session",
                )
            )
            assert job is not None
            job.completed_result = {}
            await db.commit()
    storage.transient_failure = False
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending(
            limit=1
        )
    assert queue.count == 1
    assert queue.last_message is not None
    assert str(queue.last_message.practice_session_id) == str(ready.session_id)
    assert waiting.qa_key not in storage.objects


@pytest.mark.anyio
async def test_readiness_waiting_on_cancellation_reloads_committed_session(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if db_session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking test")
    data = await _seed_round(db_session_factory, submitted=0)
    storage, queue = FakeObjectStorage(), FakeAIJobQueue()
    storage.transient_failure = True
    await _skip(db_session_factory, data, storage, queue)
    storage.transient_failure = False
    cancelled, release, contender = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with db_session_factory() as cancelling, db_session_factory() as recovering:
        stale = await recovering.get(PracticeSessionModel, data.session_id)
        assert stale is not None and stale.status is SessionStatus.REPORT_GENERATING
        cancel_uow, recover_uow = SqlAlchemyUnitOfWork(cancelling), SqlAlchemyUnitOfWork(recovering)
        update_session = cancel_uow.sessions.update
        lock_session = recover_uow.sessions.get_by_id_for_update

        async def pause_cancel(session: PracticeSession, expected_version: int) -> PracticeSession:
            result = await update_session(session, expected_version)
            cancelled.set()
            await release.wait()
            return result

        async def wait_for_cancel(session_id: UUID) -> PracticeSession | None:
            contender.set()
            return await lock_session(session_id)

        monkeypatch.setattr(cancel_uow.sessions, "update", pause_cancel)
        monkeypatch.setattr(recover_uow.sessions, "get_by_id_for_update", wait_for_cancel)
        workflow = SessionWorkflow(cancel_uow)
        cancel_task = asyncio.create_task(
            workflow.cancel(
                data.session_id,
                data.actor_id,
                "Stop",
                idempotency_key="cancel-race",
            )
        )
        await asyncio.wait_for(cancelled.wait(), timeout=5)
        recover_task = asyncio.create_task(
            AIJobs(
                recover_uow,
                storage=storage,
                queue=queue,
            ).resume_report(data.session_id)
        )
        await asyncio.wait_for(contender.wait(), timeout=5)
        release.set()
        await asyncio.wait_for(asyncio.gather(cancel_task, recover_task), timeout=5)
    async with db_session_factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AnalysisJobModel)
                .where(
                    AnalysisJobModel.job_type == "generate_report",
                )
            )
            == 0
        )
    assert queue.count == 0
    assert data.qa_key not in storage.objects


@pytest.mark.anyio
async def test_final_submitted_result_commits_round_before_storage_preparation(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await _seed_round(db_session_factory, submitted=1)
    async with db_session_factory() as db:
        question = await db.get(QuestionModel, data.final_question_id)
        round_ = await db.get(QARoundModel, data.round_id)
        previous = await db.get(AnswerModel, data.answers[0])
        assert question is not None and round_ is not None and previous is not None
        question.state = QuestionState.ANSWERED
        round_.current_question_id = None
        answer_id, job_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        db.add(
            AnswerModel(
                id=answer_id,
                qa_round_id=round_.id,
                question_id=question.id,
                answered_by=data.actor_id,
                status=AnswerStatus.SUBMITTED,
                audio_asset_version_id=previous.audio_asset_version_id,
                duration_ms=1000,
                submitted_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            AnalysisJobModel(
                id=job_id,
                practice_session_id=data.session_id,
                attempt_id=data.attempt_id,
                analysis_attempt=1,
                answer_id=answer_id,
                job_type="analyze_answer",
                status=AnalysisJobStatus.RUNNING,
                correlation_id=uuid4(),
                created_at=now,
                updated_at=now,
                last_update_sequence=1,
                payload={"trace_id": "trc_test_update"},
            )
        )
        await db.commit()
        data.answers.append(answer_id)
        data.jobs.append(job_id)
    storage, queue = FakeObjectStorage(), FakeAIJobQueue()
    _store_evidence(storage, data)
    storage.transient_failure = True
    await _complete(db_session_factory, data, 1, storage, queue, follow_up=False)
    await _complete(db_session_factory, data, 0, storage, queue)
    assert queue.count == 0
    async with db_session_factory() as db:
        session = await db.get(PracticeSessionModel, data.session_id)
        round_ = await db.get(QARoundModel, data.round_id)
        assert session is not None and session.status is SessionStatus.REPORT_GENERATING
        assert round_ is not None and round_.state is QARoundState.COMPLETED
        assert round_.follow_up_count == 0
    storage.transient_failure = False
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending()
    assert queue.count == 1
    assert len(json.loads(storage.objects[data.qa_key])["assessments"]) == 2


@pytest.mark.anyio
async def test_report_job_is_committed_before_queue_failure_and_recovered_with_same_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await _seed_round(db_session_factory, submitted=0)
    storage = FakeObjectStorage()

    class Queue(FakeAIJobQueue):
        async def enqueue(
            self,
            message: AIJobQueueEnvelope | None = None,
            *,
            envelope: AIJobQueueEnvelope | None = None,
        ) -> AIJobQueueAccepted:
            target = message if message is not None else envelope
            assert target is not None
            async with db_session_factory() as observer:
                committed = await observer.get(AnalysisJobModel, UUID(target.job_id))
                assert committed is not None
                assert committed.status is AnalysisJobStatus.PENDING
            return await super().enqueue(message, envelope=envelope)

    queue = Queue()
    queue.fail_next(1)
    await _skip(db_session_factory, data, storage, queue)
    assert queue.count == 0
    async with db_session_factory() as db:
        pending = await SqlAlchemyUnitOfWork(db).jobs.get_report_by_attempt_id(data.attempt_id)
        assert pending is not None and pending.status is AnalysisJobStatus.PENDING
        assert pending.dispatch_retry_count == 1
        stable_id = pending.id
    async with db_session_factory() as db:
        await AIJobs(SqlAlchemyUnitOfWork(db), storage=storage, queue=queue).redispatch_pending(
            now=datetime.now(UTC) + timedelta(minutes=2),
        )
    assert queue.count == 1
    assert queue.last_message is not None and queue.last_message.job_id == str(stable_id)
