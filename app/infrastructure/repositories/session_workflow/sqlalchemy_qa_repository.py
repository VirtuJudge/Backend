from dataclasses import asdict
from typing import cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.qa_repository import QARepository
from app.domain.session_workflow.entities.qa_round import Answer, QARound, Question
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.configurations.session_workflow.qa_configuration import (
    AnswerModel,
    QARoundModel,
    QuestionModel,
)


def _round(model: QARoundModel) -> QARound:
    return QARound(
        id=model.id,
        practice_session_id=model.practice_session_id,
        analysis_attempt_id=model.analysis_attempt_id,
        state=model.state,
        current_question_id=model.current_question_id,
        follow_up_count=model.follow_up_count,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _question(model: QuestionModel) -> Question:
    return Question(
        id=model.id,
        qa_round_id=model.qa_round_id,
        practice_session_id=model.practice_session_id,
        kind=model.kind,
        position=model.position,
        text=model.text,
        reason=model.reason,
        rubric_dimension=model.rubric_dimension,
        evidence_ids=list(model.evidence_ids),
        parent_answer_id=model.parent_answer_id,
        state=model.state,
        created_at=model.created_at,
    )


def _answer(model: AnswerModel) -> Answer:
    return Answer(
        id=model.id,
        qa_round_id=model.qa_round_id,
        question_id=model.question_id,
        answered_by=model.answered_by,
        status=model.status,
        audio_asset_version_id=model.audio_asset_version_id,
        duration_ms=model.duration_ms,
        transcript_artifact_id=model.transcript_artifact_id,
        assessment_artifact_id=model.assessment_artifact_id,
        submitted_at=model.submitted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        idempotency_key=model.idempotency_key,
        request_hash=model.request_hash,
    )


class SqlAlchemyQARepository(QARepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_round_by_session(self, session_id: UUID) -> QARound | None:
        model = await self._session.scalar(
            select(QARoundModel).where(QARoundModel.practice_session_id == session_id)
        )
        return _round(model) if model is not None else None

    async def get_round_for_update(self, round_id: UUID) -> QARound | None:
        model = await self._session.scalar(
            select(QARoundModel).where(QARoundModel.id == round_id).with_for_update()
        )
        return _round(model) if model is not None else None

    async def get_question(self, question_id: UUID) -> Question | None:
        model = await self._session.get(QuestionModel, question_id)
        return _question(model) if model is not None else None

    async def list_questions(self, round_id: UUID) -> list[Question]:
        models = list(
            (
                await self._session.scalars(
                    select(QuestionModel)
                    .where(QuestionModel.qa_round_id == round_id)
                    .order_by(QuestionModel.position)
                )
            ).all()
        )
        return [_question(model) for model in models]

    async def get_answer(self, answer_id: UUID) -> Answer | None:
        model = await self._session.get(AnswerModel, answer_id)
        return _answer(model) if model is not None else None

    async def get_answer_for_update(self, answer_id: UUID) -> Answer | None:
        model = await self._session.scalar(
            select(AnswerModel)
            .where(AnswerModel.id == answer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _answer(model) if model is not None else None

    async def get_answer_by_question(self, question_id: UUID) -> Answer | None:
        model = await self._session.scalar(
            select(AnswerModel).where(AnswerModel.question_id == question_id)
        )
        return _answer(model) if model is not None else None

    async def list_answers(self, round_id: UUID) -> list[Answer]:
        models = list(
            (
                await self._session.scalars(
                    select(AnswerModel)
                    .where(AnswerModel.qa_round_id == round_id)
                    .order_by(AnswerModel.created_at)
                )
            ).all()
        )
        return [_answer(model) for model in models]

    async def create_round(self, round_: QARound) -> None:
        self._session.add(QARoundModel(**asdict(round_)))
        await self._session.flush()

    async def create_questions(self, questions: list[Question]) -> None:
        self._session.add_all([QuestionModel(**asdict(question)) for question in questions])
        await self._session.flush()

    async def create_answer(self, answer: Answer) -> None:
        self._session.add(AnswerModel(**asdict(answer)))
        await self._session.flush()

    async def update_round(self, round_: QARound, expected_version: int) -> None:
        result = cast(
            CursorResult[object],
            await self._session.execute(
                update(QARoundModel)
                .where(QARoundModel.id == round_.id, QARoundModel.version == expected_version)
                .values(
                    state=round_.state,
                    current_question_id=round_.current_question_id,
                    follow_up_count=round_.follow_up_count,
                    version=round_.version,
                    updated_at=round_.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise StaleEntityVersion("Q&A Round version changed concurrently.")

    async def update_question(self, question: Question) -> None:
        await self._session.execute(
            update(QuestionModel)
            .where(QuestionModel.id == question.id)
            .values(state=question.state)
        )

    async def update_answer(self, answer: Answer) -> None:
        await self._session.execute(
            update(AnswerModel)
            .where(AnswerModel.id == answer.id)
            .values(
                answered_by=answer.answered_by,
                status=answer.status,
                audio_asset_version_id=answer.audio_asset_version_id,
                duration_ms=answer.duration_ms,
                transcript_artifact_id=answer.transcript_artifact_id,
                assessment_artifact_id=answer.assessment_artifact_id,
                submitted_at=answer.submitted_at,
                updated_at=answer.updated_at,
                idempotency_key=answer.idempotency_key,
                request_hash=answer.request_hash,
            )
        )
