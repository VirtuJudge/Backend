from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.session_workflow.entities.qa_round import Answer, QARound, Question
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.exceptions import FollowUpLimitReached, QuestionNotActive

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def _question(position: int, state: QuestionState) -> Question:
    return Question(
        id=uuid4(),
        qa_round_id=uuid4(),
        practice_session_id=uuid4(),
        kind=QuestionKind.PRIMARY,
        position=position,
        text=f"Question {position}?",
        reason="Grounded reason",
        rubric_dimension="business_reasoning",
        evidence_ids=[f"ev-{position}"],
        parent_answer_id=None,
        state=state,
        created_at=NOW,
    )


def test_finalizing_active_question_advances_to_next_question() -> None:
    active = _question(1, QuestionState.ACTIVE)
    pending = _question(2, QuestionState.PENDING)
    round_ = QARound(
        id=active.qa_round_id,
        practice_session_id=active.practice_session_id,
        analysis_attempt_id=uuid4(),
        state=QARoundState.IN_PROGRESS,
        current_question_id=active.id,
        follow_up_count=0,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    round_.finalize_answer(active, pending, AnswerStatus.SUBMITTED, NOW)

    assert active.state is QuestionState.ANSWERED
    assert pending.state is QuestionState.ACTIVE
    assert round_.current_question_id == pending.id
    assert round_.version == 2


def test_non_active_question_cannot_be_finalized() -> None:
    question = _question(1, QuestionState.PENDING)
    round_ = QARound(
        id=question.qa_round_id,
        practice_session_id=question.practice_session_id,
        analysis_attempt_id=uuid4(),
        state=QARoundState.IN_PROGRESS,
        current_question_id=uuid4(),
        follow_up_count=0,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(QuestionNotActive):
        round_.finalize_answer(question, None, AnswerStatus.SKIPPED, NOW)


def test_last_submitted_answer_keeps_round_open_while_analysis_is_pending() -> None:
    active = _question(3, QuestionState.ACTIVE)
    round_ = QARound(
        id=active.qa_round_id,
        practice_session_id=active.practice_session_id,
        analysis_attempt_id=uuid4(),
        state=QARoundState.IN_PROGRESS,
        current_question_id=active.id,
        follow_up_count=0,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    round_.finalize_answer(active, None, AnswerStatus.SUBMITTED, NOW, awaiting_analysis=True)

    assert round_.state is QARoundState.IN_PROGRESS
    assert round_.current_question_id is None
    assert round_.complete_if_idle(NOW, has_pending_questions=False)
    assert round_.state is QARoundState.COMPLETED


def test_round_rejects_third_follow_up() -> None:
    active = _question(3, QuestionState.ACTIVE)
    round_ = QARound(
        id=active.qa_round_id,
        practice_session_id=active.practice_session_id,
        analysis_attempt_id=uuid4(),
        state=QARoundState.IN_PROGRESS,
        current_question_id=None,
        follow_up_count=2,
        version=3,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(FollowUpLimitReached):
        round_.add_follow_up(uuid4(), NOW)


def test_submitted_answer_is_final() -> None:
    answer = Answer(
        id=uuid4(),
        qa_round_id=uuid4(),
        question_id=uuid4(),
        answered_by=uuid4(),
        status=AnswerStatus.SUBMITTED,
        audio_asset_version_id=uuid4(),
        duration_ms=12_000,
        transcript_artifact_id=None,
        assessment_artifact_id=None,
        submitted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )

    assert answer.is_final
