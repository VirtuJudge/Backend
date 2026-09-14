from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_asset_store
from app.api.dependencies.session_workflow import get_session_workflow
from app.api.errors import handle_asset_error, problem_response
from app.api.schemas.asset import UploadIntentResponse
from app.api.schemas.qa import (
    AnswerResponse,
    AnswerUploadIntentRequest,
    AnswerUploadIntentResponse,
    QARoundResponse,
    QuestionResponse,
    SkipQuestionRequest,
    SubmitAnswerRequest,
)
from app.application.services.asset_store import AssetStore
from app.application.session_workflow import SessionWorkflow
from app.domain.asset import AssetDomainError
from app.domain.session_workflow.entities.qa_round import Answer, Question
from app.domain.session_workflow.exceptions import (
    AnswerAlreadyFinalized,
    AnswerNotFound,
    QuestionNotActive,
    QuestionNotFound,
    QuestionsNotReady,
    SessionNotFoundError,
    UnauthorizedSessionAction,
    UnverifiedAsset,
)
from app.domain.user import User

router = APIRouter(prefix="/api/v1", tags=["Q&A"])


def _answer(answer: Answer) -> AnswerResponse:
    return AnswerResponse(
        id=answer.id,
        question_id=answer.question_id,
        answered_by=answer.answered_by,
        status=answer.status,
        audio_asset_version_id=answer.audio_asset_version_id,
        transcript_artifact_id=answer.transcript_artifact_id,
        duration_ms=answer.duration_ms,
        submitted_at=answer.submitted_at,
    )


def _question(question: Question) -> QuestionResponse:
    return QuestionResponse(
        id=question.id,
        practice_session_id=question.practice_session_id,
        kind=question.kind,
        position=question.position,
        text=question.text,
        reason=question.reason,
        rubric_dimension=question.rubric_dimension,
        evidence_ids=question.evidence_ids,
        parent_answer_id=question.parent_answer_id,
        state=question.state,
    )


def _qa_error(error: Exception, request: Request) -> Any:
    if isinstance(error, UnauthorizedSessionAction):
        return problem_response(403, "forbidden", "Forbidden", str(error), request.url.path)
    if isinstance(error, (QuestionNotFound, AnswerNotFound, SessionNotFoundError)):
        return problem_response(
            404,
            "not_found",
            "Resource not found",
            "The requested resource was not found.",
            request.url.path,
        )
    if isinstance(error, QuestionsNotReady):
        return problem_response(
            409, "questions_not_ready", "Questions not ready", str(error), request.url.path
        )
    if isinstance(error, QuestionNotActive):
        return problem_response(
            409, "question_not_active", "Question not active", str(error), request.url.path
        )
    if isinstance(error, AnswerAlreadyFinalized):
        return problem_response(
            409, "already_submitted", "Answer already finalized", str(error), request.url.path
        )
    if isinstance(error, UnverifiedAsset):
        return problem_response(
            422, "invalid_answer_audio", "Invalid answer audio", str(error), request.url.path
        )
    raise error


@router.get("/practice-sessions/{session_id}/qa", response_model=QARoundResponse)
async def get_qa_round(
    session_id: UUID,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        round_, questions, answers = await workflow.get_qa_round(session_id, current_user.id)
    except Exception as error:
        return _qa_error(error, request)
    response.headers["ETag"] = f'"{round_.version}"'
    return QARoundResponse(
        id=round_.id,
        practice_session_id=round_.practice_session_id,
        state=round_.state,
        questions=[_question(question) for question in questions],
        answers=[_answer(answer) for answer in answers],
        current_question_id=round_.current_question_id,
        follow_up_count=round_.follow_up_count,
        version=round_.version,
    )


@router.post(
    "/questions/{question_id}/answer-upload-intents",
    response_model=AnswerUploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_answer_upload_intent(
    question_id: UUID,
    payload: AnswerUploadIntentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    asset_store: AssetStore = Depends(get_asset_store),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        answer, intent = await workflow.create_answer_upload_intent(
            question_id=question_id,
            actor_id=current_user.id,
            file_name=payload.file_name,
            declared_media_type=payload.declared_media_type,
            declared_size_bytes=payload.declared_size_bytes,
            idempotency_key=idempotency_key,
            asset_store=asset_store,
        )
    except AssetDomainError as error:
        return handle_asset_error(error, request.url.path)
    except Exception as error:
        return _qa_error(error, request)
    return AnswerUploadIntentResponse(
        answer=_answer(answer),
        upload_intent=UploadIntentResponse(
            asset_id=intent.asset_id,
            asset_version_id=intent.asset_version_id,
            upload_url=intent.upload_url,
            method=intent.method,
            required_headers=intent.required_headers,
            expires_at=intent.expires_at,
            maximum_size_bytes=intent.maximum_size_bytes,
        ),
    )


@router.post("/answers/{answer_id}/submit", response_model=AnswerResponse, status_code=202)
async def submit_answer(
    answer_id: UUID,
    payload: SubmitAnswerRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        answer = await workflow.submit_answer(
            answer_id=answer_id,
            actor_id=current_user.id,
            checksum=payload.checksum,
            size_bytes=payload.size_bytes,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        return _qa_error(error, request)
    return _answer(answer)


@router.post("/questions/{question_id}/skip", response_model=AnswerResponse)
async def skip_question(
    question_id: UUID,
    payload: SkipQuestionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        answer = await workflow.skip_answer(
            question_id=question_id,
            actor_id=current_user.id,
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        return _qa_error(error, request)
    return _answer(answer)
