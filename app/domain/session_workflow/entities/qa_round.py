from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.exceptions import FollowUpLimitReached, QuestionNotActive


@dataclass(slots=True)
class QARound:
    id: UUID
    practice_session_id: UUID
    analysis_attempt_id: UUID
    state: QARoundState
    current_question_id: UUID | None
    follow_up_count: int
    version: int
    created_at: datetime
    updated_at: datetime

    def finalize_answer(
        self,
        question: "Question",
        next_question: "Question | None",
        answer_status: AnswerStatus,
        at: datetime,
        *,
        awaiting_analysis: bool = False,
    ) -> None:
        if (
            self.state is not QARoundState.IN_PROGRESS
            or self.current_question_id != question.id
            or question.state is not QuestionState.ACTIVE
        ):
            raise QuestionNotActive("Only the active question accepts an answer or skip.")
        question.state = (
            QuestionState.SKIPPED
            if answer_status is AnswerStatus.SKIPPED
            else QuestionState.ANSWERED
        )
        if next_question is None:
            self.current_question_id = None
            self.state = QARoundState.IN_PROGRESS if awaiting_analysis else QARoundState.COMPLETED
        else:
            next_question.state = QuestionState.ACTIVE
            self.current_question_id = next_question.id
        self.version += 1
        self.updated_at = at

    def complete_if_idle(self, at: datetime, *, has_pending_questions: bool) -> bool:
        if self.current_question_id is not None or has_pending_questions:
            return False
        if self.state is QARoundState.COMPLETED:
            return False
        self.state = QARoundState.COMPLETED
        self.version += 1
        self.updated_at = at
        return True

    def add_follow_up(self, question_id: UUID, at: datetime, *, activate: bool = True) -> None:
        if self.follow_up_count >= 2:
            raise FollowUpLimitReached("A Q&A Round cannot contain more than two follow-ups.")
        self.follow_up_count += 1
        if activate:
            self.current_question_id = question_id
        self.state = QARoundState.IN_PROGRESS
        self.version += 1
        self.updated_at = at


@dataclass(slots=True)
class Question:
    id: UUID
    qa_round_id: UUID
    practice_session_id: UUID
    kind: QuestionKind
    position: int
    text: str
    reason: str
    rubric_dimension: str
    evidence_ids: list[str]
    parent_answer_id: UUID | None
    state: QuestionState
    created_at: datetime


@dataclass(slots=True)
class Answer:
    id: UUID
    qa_round_id: UUID
    question_id: UUID
    answered_by: UUID
    status: AnswerStatus
    audio_asset_version_id: UUID | None
    duration_ms: int | None
    transcript_artifact_id: str | None
    assessment_artifact_id: str | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    idempotency_key: str | None = None
    request_hash: str | None = None

    @property
    def is_final(self) -> bool:
        return self.status in {AnswerStatus.SUBMITTED, AnswerStatus.SKIPPED}
