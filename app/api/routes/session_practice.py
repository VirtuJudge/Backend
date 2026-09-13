from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.session_workflow import get_session_workflow
from app.api.schemas.session_practice import (
    AnalysisAttemptListResponse,
    AnalysisAttemptResponse,
    CancelPracticeSessionRequest,
    CreateAnalysisAttemptRequest,
    CreatePracticeSessionRequest,
    PracticeSessionListResponse,
    PracticeSessionResponse,
)
from app.application.session_workflow import SessionWorkflow
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.exceptions import (
    ConsentRequiredError,
    InvalidSessionStatusTransition,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnauthorizedSessionAction,
)
from app.domain.user import User

router = APIRouter(
    prefix="/api/v1",
    tags=["Practice Sessions"],
)


def parse_etag(if_match: str) -> int:
    value = if_match.strip()

    if value.startswith("W/"):
        value = value[2:].strip()

    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]

    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid If-Match header.",
        ) from exc


@router.post(
    "/projects/{project_id}/practice-sessions",
    response_model=PracticeSessionResponse,
    status_code=201,
)
async def create_practice_session(
    project_id: UUID,
    request: CreatePracticeSessionRequest,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> PracticeSessionResponse:
    try:
        session = await workflow.create_session(
            project_id=project_id,
            actor_id=current_user.id,
            name=request.name,
            presentation_asset_version_id=request.presentation_asset_version_id,
            document_version_id=request.document_version_id,
        )
    except UnauthorizedSessionAction as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized to create a session for this project.",
        ) from error
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return PracticeSessionResponse.model_validate(session)


@router.get(
    "/practice_sessions/{session_id}",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def get_practice_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> PracticeSessionResponse:
    try:
        session = await workflow.get_session(session_id=session_id, actor_id=current_user.id)
    except UnauthorizedSessionAction as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return PracticeSessionResponse.model_validate(session)


@router.get(
    "/projects/{project_id}/practice_sessions",
    response_model=PracticeSessionListResponse,
    status_code=200,
)
async def list_practice_sessions(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    search: str | None = Query(None, alias="search"),
    cursor: str | None = Query(None, alias="cursor"),
    limit: int = Query(20, ge=1, le=100),
) -> PracticeSessionListResponse:
    # Implement the logic to list practice sessions
    try:
        sessions, next_cursor = await workflow.list_sessions(
            actor_id=current_user.id,
            project_id=project_id,
            cursor=cursor,
            search=search,
            limit=limit,
        )
    except UnauthorizedSessionAction as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return PracticeSessionListResponse(
        items=[PracticeSessionResponse.model_validate(session) for session in sessions],
        next_cursor=next_cursor,
    )


@router.patch(
    "/practice_sessions/{session_id}",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def update_practice_session(
    session_id: UUID,
    request: CreatePracticeSessionRequest,
    if_match: str = Header(..., alias="If-Match"),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> PracticeSessionResponse:
    try:
        session = await workflow.update_session(
            session_id=session_id,
            actor_id=current_user.id,
            expected_version=parse_etag(if_match),
            name=request.name,
            presentation_version_id=request.presentation_asset_version_id,
            document_version_id=request.document_version_id,
        )
    except UnauthorizedSessionAction as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except StaleEntityVersion as error:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(error)
        ) from error
    except ManifestAlreadyFrozen as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return PracticeSessionResponse.model_validate(session)


@router.post(
    "/practice_sessions/{session_id}/analysis-attempts",
    response_model=AnalysisAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_analysis_attempt(
    session_id: UUID,
    request: CreateAnalysisAttemptRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
    ),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> AnalysisAttemptResponse:

    try:
        attempt = await workflow.create_analysis_attempt(
            session_id=session_id,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
            consent_accepted=request.consent.accepted,
        )

    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except UnauthorizedSessionAction as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    except SessionNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_not_ready",
                "message": str(error),
            },
        ) from error

    except ConsentRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "consent_required",
                "message": str(error),
            },
        ) from error

    except ManifestAlreadyFrozen as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    return AnalysisAttemptResponse.model_validate(attempt)


@router.post(
    "/practice_sessions/{session_id}/retries",
    response_model=AnalysisAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_analysis_attempt(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> AnalysisAttemptResponse:
    try:
        attempt = await workflow.retry(
            session_id=session_id,
            actor_id=current_user.id,
        )
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except UnauthorizedSessionAction as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RetryNotAllowed as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "retry_not_allowed",
                "message": str(error),
            },
        ) from error

    return AnalysisAttemptResponse.model_validate(attempt)


@router.get(
    "/practice_sessions/{session_id}/analysis-attempts",
    response_model=AnalysisAttemptListResponse,
    status_code=status.HTTP_200_OK,
)
async def get_analysis_attempts(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    cursor: str | None = Query(None, alias="cursor"),
    limit: int = Query(20, ge=1, le=100),
) -> AnalysisAttemptListResponse:

    try:
        attempts = await workflow.get_analysis_attemptss(
            session_id=session_id,
            actor_id=current_user.id,
            cursor=cursor,
            limit=limit,
        )

    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except UnauthorizedSessionAction as error:
        raise HTTPException(
            status_code=403,
            detail=str(error),
        ) from error

    return AnalysisAttemptListResponse(
        items=[AnalysisAttemptResponse.model_validate(attempt) for attempt in attempts],
        next_cursor=cursor,
    )


@router.post(
    "/practice_sessions/{session_id}/cancel",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_practice_session(
    session_id: UUID,
    request: CancelPracticeSessionRequest,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> PracticeSessionResponse:
    try:
        session = await workflow.cancel(
            session_id=session_id,
            actor_id=current_user.id,
            reason=request.reason,
        )
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except UnauthorizedSessionAction as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except InvalidSessionStatusTransition as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    return PracticeSessionResponse.model_validate(session)


# @router.put(
#     "/practice_sessions/{session_id}/speaker-mappings",
#     response_model=list[SpeakerMappingResponse],
# )
# async def update_speaker_mappings(
#     session_id: UUID,
#     request: UpdateSpeakerMappingsRequest,
#     if_match: str = Header(...),
#     current_user: User = Depends(get_current_user),
#     workflow: SessionWorkflow = Depends(get_session_workflow),
# ) -> list[SpeakerMappingResponse]:
#     mappings = await workflow.update_speaker_mappings(
#         session_id=session_id,
#         mappings=request.mappings,
#         actor_id=current_user.id,
#         expected_version=parse_etag(if_match),
#     )

#     return [
#         SpeakerMappingResponse.from_domain(mapping)
#         for mapping in mappings
#     ]
