import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.session_workflow import SessionWorkflow
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.qa_round import Answer, QARound, Question
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    AnswerDurationExceeded,
    UnauthorizedSessionAction,
)
from tests.support.fake_ai_job_queue import FakeAIJobQueue

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def _setup() -> tuple[SessionWorkflow, MagicMock, QARound, list[Question], PracticeSession]:
    session_id = uuid4()
    project_id = uuid4()
    attempt_id = uuid4()
    round_id = uuid4()
    actor_id = uuid4()
    session = PracticeSession(
        id=session_id,
        project_id=project_id,
        created_by=actor_id,
        name="Pitch",
        status=SessionStatus.QUESTIONS_IN_PROGRESS,
        version=2,
        created_at=NOW,
        updated_at=NOW,
        consent_granted=True,
        started_at=NOW,
        completed_at=None,
        cancelled_at=None,
    )
    questions = [
        Question(
            id=uuid4(),
            qa_round_id=round_id,
            practice_session_id=session_id,
            kind=QuestionKind.PRIMARY,
            position=index,
            text=f"Question {index}?",
            reason="Grounded",
            rubric_dimension="business_reasoning",
            evidence_ids=[f"ev-{index}"],
            parent_answer_id=None,
            state=QuestionState.ACTIVE if index == 1 else QuestionState.PENDING,
            created_at=NOW,
        )
        for index in range(1, 4)
    ]
    round_ = QARound(
        id=round_id,
        practice_session_id=session_id,
        analysis_attempt_id=attempt_id,
        state=QARoundState.IN_PROGRESS,
        current_question_id=questions[0].id,
        follow_up_count=0,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.sessions.get_by_id = AsyncMock(return_value=session)
    uow.projects.is_member = AsyncMock(return_value=True)
    uow.qa.get_question = AsyncMock(
        side_effect=lambda question_id: next(
            (question for question in questions if question.id == question_id), None
        )
    )
    uow.qa.get_round_for_update = AsyncMock(return_value=round_)
    uow.qa.get_answer_for_update = AsyncMock(return_value=None)
    uow.qa.get_answer_by_question = AsyncMock(return_value=None)
    uow.qa.list_questions = AsyncMock(return_value=questions)
    uow.qa.create_answer = AsyncMock()
    uow.qa.update_answer = AsyncMock()
    uow.qa.update_question = AsyncMock()
    uow.qa.update_round = AsyncMock()
    uow.commit = AsyncMock()
    uow.jobs.get_by_answer_id = AsyncMock(return_value=None)
    uow.jobs.create = AsyncMock()
    uow.jobs.change_pending_to_queued = AsyncMock(
        side_effect=lambda job_id, now, payload=None: None
    )
    uow.jobs.record_dispatch_failure = AsyncMock()
    queue = FakeAIJobQueue()
    return SessionWorkflow(uow, queue=queue), uow, round_, questions, session


@pytest.mark.asyncio
async def test_skipping_active_question_records_skip_and_advances() -> None:
    workflow, uow, round_, questions, session = _setup()

    answer = await workflow.skip_answer(
        question_id=questions[0].id,
        actor_id=session.created_by,
        reason="Need more research",
        idempotency_key="skip-1",
    )

    assert answer.status is AnswerStatus.SKIPPED
    assert questions[0].state is QuestionState.SKIPPED
    assert questions[1].state is QuestionState.ACTIVE
    assert round_.current_question_id == questions[1].id
    uow.qa.create_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_replaying_skip_returns_the_original_answer() -> None:
    workflow, uow, _round, questions, session = _setup()
    first = await workflow.skip_answer(
        question_id=questions[0].id,
        actor_id=session.created_by,
        reason="Need more research",
        idempotency_key="skip-1",
    )
    uow.qa.get_answer_by_question.return_value = first

    replay = await workflow.skip_answer(
        question_id=questions[0].id,
        actor_id=session.created_by,
        reason="Need more research",
        idempotency_key="skip-1",
    )

    assert replay.id == first.id
    uow.qa.create_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_submitting_verified_audio_creates_one_answer_analysis_job() -> None:
    workflow, uow, round_, questions, session = _setup()
    answer = Answer(
        id=uuid4(),
        qa_round_id=round_.id,
        question_id=questions[0].id,
        answered_by=session.created_by,
        status=AnswerStatus.DRAFT,
        audio_asset_version_id=uuid4(),
        duration_ms=None,
        transcript_artifact_id=None,
        assessment_artifact_id=None,
        submitted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    checksum = "sha256:" + "a" * 64
    uow.qa.get_answer = AsyncMock(return_value=answer)
    uow.qa.get_answer_for_update.return_value = answer
    uow.projects.get_verified_asset_version_snapshot = AsyncMock(
        return_value={
            "artifact_id": str(answer.audio_asset_version_id),
            "object_key": "answers/audio.webm",
            "checksum": checksum,
            "media_type": "audio/webm",
            "duration_ms": 84_000,
            "size_bytes": 1234,
        }
    )
    uow.attempts.get_by_id = AsyncMock(
        return_value=AnalysisAttempt(
            id=round_.analysis_attempt_id,
            session_id=session.id,
            manifest_id=uuid4(),
            idempotency_key=None,
            attempt_number=1,
            status=AnalysisAttemptStatus.COMPLETED,
            failure_code=None,
            failure_message=None,
            created_at=NOW,
            started_at=NOW,
            completed_at=NOW,
            failed_at=None,
            cancelled_at=None,
            version=2,
        )
    )

    result = await workflow.submit_answer(
        answer_id=answer.id,
        actor_id=session.created_by,
        checksum=checksum,
        size_bytes=1234,
        idempotency_key="submit-1",
    )

    assert result.status is AnswerStatus.SUBMITTED
    assert result.duration_ms == 84_000
    created_job = uow.jobs.create.await_args.args[0]
    assert created_job.job_type == "analyze_answer"
    assert created_job.answer_id == answer.id
    assert created_job.payload["payload"]["remaining_follow_ups"] == 2


@pytest.mark.asyncio
async def test_concurrent_same_key_submit_reloads_after_round_lock() -> None:
    workflow, uow, round_, questions, session = _setup()
    answer = Answer(
        id=uuid4(),
        qa_round_id=round_.id,
        question_id=questions[0].id,
        answered_by=session.created_by,
        status=AnswerStatus.DRAFT,
        audio_asset_version_id=uuid4(),
        duration_ms=None,
        transcript_artifact_id=None,
        assessment_artifact_id=None,
        submitted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    checksum = "sha256:" + "a" * 64

    async def load_final_answer(_answer_id: object) -> Answer:
        answer.status = AnswerStatus.SUBMITTED
        answer.idempotency_key = "submit-1"
        answer.request_hash = hashlib.sha256(f"{checksum}:1234".encode()).hexdigest()
        round_.current_question_id = questions[1].id
        return answer

    uow.qa.get_answer = AsyncMock(return_value=answer)
    uow.qa.get_answer_for_update.side_effect = load_final_answer

    result = await workflow.submit_answer(
        answer_id=answer.id,
        actor_id=session.created_by,
        checksum=checksum,
        size_bytes=1234,
        idempotency_key="submit-1",
    )

    assert result is answer
    uow.qa.get_answer_for_update.assert_awaited_once_with(answer.id)
    uow.jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_rejects_audio_over_public_duration_limit() -> None:
    workflow, uow, round_, questions, session = _setup()
    answer = Answer(
        id=uuid4(),
        qa_round_id=round_.id,
        question_id=questions[0].id,
        answered_by=session.created_by,
        status=AnswerStatus.DRAFT,
        audio_asset_version_id=uuid4(),
        duration_ms=None,
        transcript_artifact_id=None,
        assessment_artifact_id=None,
        submitted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    checksum = "sha256:" + "b" * 64
    uow.qa.get_answer = AsyncMock(return_value=answer)
    uow.qa.get_answer_for_update.return_value = answer
    uow.projects.get_verified_asset_version_snapshot = AsyncMock(
        return_value={
            "checksum": checksum,
            "size_bytes": 1234,
            "duration_ms": 120_001,
        }
    )

    with pytest.raises(AnswerDurationExceeded):
        await workflow.submit_answer(
            answer_id=answer.id,
            actor_id=session.created_by,
            checksum=checksum,
            size_bytes=1234,
            idempotency_key="submit-long",
        )


@pytest.mark.asyncio
async def test_get_qa_round_rejects_project_outsider() -> None:
    workflow, uow, _round, _questions, session = _setup()
    uow.projects.is_member.return_value = False

    with pytest.raises(UnauthorizedSessionAction):
        await workflow.get_qa_round(session.id, uuid4())
