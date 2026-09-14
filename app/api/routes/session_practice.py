from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.session_notifications import get_session_notifications
from app.api.dependencies.session_workflow import get_session_workflow
from app.api.errors import problem_response
from app.api.schemas.session_practice import (
    AnalysisAttemptListResponse,
    AnalysisAttemptResponse,
    CancelPracticeSessionRequest,
    CreateAnalysisAttemptRequest,
    CreatePracticeSessionRequest,
    PracticeSessionListResponse,
    PracticeSessionResponse,
    ProblemDetails,
    RubricSpec,
    SessionManifestResponse,
    UpdatePracticeSessionRequest,
)
from app.api.schemas.speaker_mapping import (
    SpeakerMappingResponse,
    UpdateSpeakerMappingsRequest,
)
from app.api.session_event_stream import stream_live_session_events
from app.application.ports.session_notification import SessionNotificationPort
from app.application.session_workflow import SessionWorkflow
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.exceptions import (
    AnalysisNotReady,
    ConsentPolicyOutdated,
    ConsentRequiredError,
    IdempotencyConflict,
    InvalidManifestDocuments,
    InvalidSessionStatusTransition,
    InvalidSpeakerLabel,
    InvalidTeamMember,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnauthorizedSessionAction,
    UnverifiedAsset,
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
    return int(value)


async def _to_session_response(
    session: PracticeSession, workflow: SessionWorkflow
) -> PracticeSessionResponse:
    manifest = await workflow.get_manifest(session.id)
    manifest_resp = SessionManifestResponse.model_validate(manifest) if manifest else None
    rubric_resp = (
        RubricSpec(rubric_id=manifest.rubric_id, version=manifest.rubric_version)
        if manifest
        else RubricSpec()
    )
    return PracticeSessionResponse(
        id=session.id,
        project_id=session.project_id,
        created_by=session.created_by,
        name=session.name,
        status=session.status,
        version=session.version,
        created_at=session.created_at,
        updated_at=session.updated_at,
        manifest=manifest_resp,
        rubric=rubric_resp,
    )


def _problem_response_doc(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": ProblemDetails.model_json_schema(),
            }
        },
    }


@router.post(
    "/projects/{project_id}/practice-sessions",
    response_model=PracticeSessionResponse,
    status_code=201,
    responses={
        201: {
            "headers": {
                "Location": {
                    "description": "URL of the created practice session",
                    "schema": {"type": "string"},
                },
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                },
            },
        },
        400: _problem_response_doc("Bad Request"),
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Project not found"),
        409: _problem_response_doc("Conflict"),
        422: _problem_response_doc("Validation failed"),
    },
)
async def create_practice_session(
    project_id: UUID,
    request: CreatePracticeSessionRequest,
    raw_request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        session = await workflow.create_session(
            project_id=project_id,
            actor_id=current_user.id,
            name=request.name,
            presentation_asset_version_id=request.presentation_asset_version_id,
            supporting_document_version_ids=request.supporting_document_version_ids,
            rubric_id=request.rubric.rubric_id if request.rubric else "startup_pitch",
            rubric_version=request.rubric.version if request.rubric else 1,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except ProjectNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Project not found",
            str(error),
            raw_request.url.path,
        )
    except UnverifiedAsset as error:
        return problem_response(
            status.HTTP_400_BAD_REQUEST,
            "unverified_asset",
            "Unverified asset",
            str(error),
            raw_request.url.path,
        )
    except InvalidManifestDocuments as error:
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_failed",
            "Validation failed",
            str(error),
            raw_request.url.path,
        )

    response.headers["ETag"] = f'"{session.version}"'
    response.headers["Location"] = f"/api/v1/practice-sessions/{session.id}"
    return await _to_session_response(session, workflow)


@router.get(
    "/practice-sessions/{session_id}",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                },
            },
        },
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
    },
)
async def get_practice_session(
    session_id: UUID,
    raw_request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        session = await workflow.get_session(session_id=session_id, actor_id=current_user.id)
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )

    response.headers["ETag"] = f'"{session.version}"'
    return await _to_session_response(session, workflow)


@router.get(
    "/practice-sessions/{session_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        400: _problem_response_doc("Invalid Last-Event-ID"),
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
    },
)
async def stream_practice_session_events(
    session_id: UUID,
    raw_request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    notifications: SessionNotificationPort = Depends(get_session_notifications),
) -> Any:
    parsed_last_event_id: int | None = None
    if last_event_id is not None:
        try:
            parsed_last_event_id = int(last_event_id)
        except ValueError:
            parsed_last_event_id = -1
        if parsed_last_event_id < 0:
            return problem_response(
                status.HTTP_400_BAD_REQUEST,
                "invalid_last_event_id",
                "Invalid Last-Event-ID",
                "Last-Event-ID must be a non-negative integer.",
                raw_request.url.path,
            )

    try:
        await workflow.get_session(session_id=session_id, actor_id=current_user.id)
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )

    return StreamingResponse(
        stream_live_session_events(
            raw_request,
            notifications,
            str(session_id),
            parsed_last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/projects/{project_id}/practice-sessions",
    response_model=PracticeSessionListResponse,
    status_code=200,
    responses={
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Project not found"),
    },
)
async def list_practice_sessions(
    project_id: UUID,
    raw_request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    search: str | None = Query(None, alias="search"),
    cursor: str | None = Query(None, alias="cursor"),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    try:
        sessions, next_cursor = await workflow.list_sessions(
            actor_id=current_user.id,
            project_id=project_id,
            cursor=cursor,
            search=search,
            limit=limit,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    items = [await _to_session_response(session, workflow) for session in sessions]
    return PracticeSessionListResponse(
        items=items,
        next_cursor=next_cursor,
    )


@router.patch(
    "/practice-sessions/{session_id}",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                },
            },
        },
        400: _problem_response_doc("Invalid header or request"),
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
        409: _problem_response_doc("Manifest already frozen or conflict"),
        412: _problem_response_doc("Precondition Failed"),
        422: _problem_response_doc("Validation failed"),
    },
)
async def update_practice_session(
    session_id: UUID,
    request: UpdatePracticeSessionRequest,
    raw_request: Request,
    response: Response,
    if_match: str = Header(..., alias="If-Match"),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        expected_version = parse_etag(if_match)
    except ValueError:
        return problem_response(
            status.HTTP_400_BAD_REQUEST,
            "invalid_header",
            "Invalid header",
            "Invalid If-Match header.",
            raw_request.url.path,
        )

    try:
        session = await workflow.update_session(
            session_id=session_id,
            actor_id=current_user.id,
            expected_version=expected_version,
            name=request.name,
            presentation_version_id=request.presentation_asset_version_id,
            supporting_document_version_ids=request.supporting_document_version_ids,
            rubric_id=request.rubric.rubric_id if request.rubric else None,
            rubric_version=request.rubric.version if request.rubric else None,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except StaleEntityVersion as error:
        return problem_response(
            status.HTTP_412_PRECONDITION_FAILED,
            "precondition_failed",
            "Precondition failed",
            str(error),
            raw_request.url.path,
        )
    except ManifestAlreadyFrozen as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "manifest_frozen",
            "Manifest frozen",
            str(error),
            raw_request.url.path,
        )
    except UnverifiedAsset as error:
        return problem_response(
            status.HTTP_400_BAD_REQUEST,
            "unverified_asset",
            "Unverified asset",
            str(error),
            raw_request.url.path,
        )
    except InvalidManifestDocuments as error:
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_failed",
            "Validation failed",
            str(error),
            raw_request.url.path,
        )

    response.headers["ETag"] = f'"{session.version}"'
    return await _to_session_response(session, workflow)


@router.post(
    "/practice-sessions/{session_id}/analysis-attempts",
    response_model=AnalysisAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "headers": {
                "Location": {
                    "description": "URL of the created analysis attempt",
                    "schema": {"type": "string"},
                },
            },
        },
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
        409: _problem_response_doc("Session not ready or conflict"),
        412: _problem_response_doc("Precondition Failed"),
        422: _problem_response_doc("Consent required or validation failed"),
    },
)
async def create_analysis_attempt(
    session_id: UUID,
    request_data: CreateAnalysisAttemptRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=128),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        attempt = await workflow.create_analysis_attempt(
            session_id=session_id,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
            consent_accepted=request_data.consent.accepted,
            consent_policy_version=request_data.consent.policy_version,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except SessionNotReadyError as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "session_not_ready",
            "Session not ready",
            str(error),
            raw_request.url.path,
        )
    except UnverifiedAsset as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "session_not_ready",
            "Session not ready",
            str(error),
            raw_request.url.path,
        )
    except (ConsentRequiredError, ConsentPolicyOutdated) as error:
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "consent_required",
            "Consent required",
            str(error),
            raw_request.url.path,
        )
    except ManifestAlreadyFrozen as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "conflict",
            "Conflict",
            str(error),
            raw_request.url.path,
        )
    except StaleEntityVersion as error:
        return problem_response(
            status.HTTP_412_PRECONDITION_FAILED,
            "precondition_failed",
            "Precondition failed",
            str(error),
            raw_request.url.path,
        )
    except IdempotencyConflict as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "Idempotency conflict",
            str(error),
            raw_request.url.path,
        )

    response.headers["Location"] = (
        f"/api/v1/practice-sessions/{session_id}/analysis-attempts/{attempt.id}"
    )
    return AnalysisAttemptResponse.model_validate(attempt)


@router.post(
    "/practice-sessions/{session_id}/retries",
    response_model=AnalysisAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "headers": {
                "Location": {
                    "description": "URL of the created analysis attempt",
                    "schema": {"type": "string"},
                },
            },
        },
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
        409: _problem_response_doc("Retry not allowed or conflict"),
        412: _problem_response_doc("Precondition Failed"),
        422: _problem_response_doc("Validation failed"),
    },
)
async def retry_analysis_attempt(
    session_id: UUID,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=128),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        attempt = await workflow.retry(
            session_id=session_id,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except RetryNotAllowed as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "retry_not_allowed",
            "Retry not allowed",
            str(error),
            raw_request.url.path,
        )
    except StaleEntityVersion as error:
        return problem_response(
            status.HTTP_412_PRECONDITION_FAILED,
            "precondition_failed",
            "Precondition failed",
            str(error),
            raw_request.url.path,
        )
    except IdempotencyConflict as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "Idempotency conflict",
            str(error),
            raw_request.url.path,
        )

    response.headers["Location"] = (
        f"/api/v1/practice-sessions/{session_id}/analysis-attempts/{attempt.id}"
    )
    return AnalysisAttemptResponse.model_validate(attempt)


@router.get(
    "/practice-sessions/{session_id}/analysis-attempts",
    response_model=AnalysisAttemptListResponse,
    status_code=status.HTTP_200_OK,
    responses={
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
    },
)
async def get_analysis_attempts(
    session_id: UUID,
    raw_request: Request,
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
    cursor: str | None = Query(None, alias="cursor"),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    try:
        attempts, next_cursor = await workflow.get_analysis_attempts(
            session_id=session_id,
            actor_id=current_user.id,
            cursor=cursor,
            limit=limit,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )

    return AnalysisAttemptListResponse(
        items=[AnalysisAttemptResponse.model_validate(attempt) for attempt in attempts],
        next_cursor=next_cursor,
    )


@router.post(
    "/practice-sessions/{session_id}/cancel",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "headers": {
                "Location": {
                    "description": "URL of the cancelled practice session",
                    "schema": {"type": "string"},
                },
                "ETag": {
                    "description": "Version entity tag of the session",
                    "schema": {"type": "string"},
                },
            },
        },
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
        409: _problem_response_doc("Terminal state or conflict"),
        412: _problem_response_doc("Precondition Failed"),
    },
)
async def cancel_practice_session(
    session_id: UUID,
    raw_request: Request,
    response: Response,
    request: CancelPracticeSessionRequest = CancelPracticeSessionRequest(),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=128),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        session = await workflow.cancel(
            session_id=session_id,
            actor_id=current_user.id,
            reason=request.reason,
            idempotency_key=idempotency_key,
        )
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except InvalidSessionStatusTransition as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "terminal_state",
            "Terminal state",
            str(error),
            raw_request.url.path,
        )
    except IdempotencyConflict as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "Idempotency conflict",
            str(error),
            raw_request.url.path,
        )
    except StaleEntityVersion as error:
        return problem_response(
            status.HTTP_412_PRECONDITION_FAILED,
            "precondition_failed",
            "Precondition failed",
            str(error),
            raw_request.url.path,
        )

    response.headers["Location"] = f"/api/v1/practice-sessions/{session.id}"
    response.headers["ETag"] = f'"{session.version}"'
    return await _to_session_response(session, workflow)


@router.put(
    "/practice-sessions/{session_id}/speaker-mappings",
    response_model=list[SpeakerMappingResponse],
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Version entity tag of the session",
                    "schema": {"type": "string"},
                },
            },
        },
        400: _problem_response_doc("Invalid If-Match header"),
        403: _problem_response_doc("Forbidden"),
        404: _problem_response_doc("Session not found"),
        409: _problem_response_doc("Analysis not ready"),
        412: _problem_response_doc("Precondition Failed"),
        422: _problem_response_doc("Invalid speaker label or team member"),
    },
)
async def update_speaker_mappings(
    session_id: UUID,
    payload: UpdateSpeakerMappingsRequest,
    raw_request: Request,
    response: Response,
    if_match: str = Header(..., alias="If-Match"),
    current_user: User = Depends(get_current_user),
    workflow: SessionWorkflow = Depends(get_session_workflow),
) -> Any:
    try:
        expected_version = parse_etag(if_match)
    except ValueError:
        return problem_response(
            status.HTTP_400_BAD_REQUEST,
            "invalid_header",
            "Invalid header",
            "Invalid If-Match header.",
            raw_request.url.path,
        )

    now = datetime.now(UTC)
    domain_mappings = [
        SpeakerMapping(
            id=uuid4(),
            attempt_id=uuid4(),
            speaker_label=m.speaker_label,
            member_id=None,
            mapped_by=current_user.id,
            mapped_at=now,
            user_id=m.user_id,
        )
        for m in payload.mappings
    ]

    try:
        mappings = await workflow.update_speaker_mappings(
            session_id=session_id,
            actor_id=current_user.id,
            expected_version=expected_version,
            mappings=domain_mappings,
        )
        session = await workflow.get_session(session_id=session_id, actor_id=current_user.id)
    except SessionNotFoundError as error:
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Session not found",
            str(error),
            raw_request.url.path,
        )
    except UnauthorizedSessionAction as error:
        return problem_response(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Forbidden",
            str(error),
            raw_request.url.path,
        )
    except StaleEntityVersion as error:
        return problem_response(
            status.HTTP_412_PRECONDITION_FAILED,
            "precondition_failed",
            "Precondition failed",
            str(error),
            raw_request.url.path,
        )
    except AnalysisNotReady as error:
        return problem_response(
            status.HTTP_409_CONFLICT,
            "analysis_not_ready",
            "Analysis not ready",
            str(error),
            raw_request.url.path,
        )
    except InvalidSpeakerLabel as error:
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_label",
            "Invalid speaker label",
            str(error),
            raw_request.url.path,
        )
    except InvalidTeamMember as error:
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_member",
            "Invalid team member",
            str(error),
            raw_request.url.path,
        )

    response.headers["ETag"] = f'"{session.version}"'
    return [SpeakerMappingResponse.model_validate(m) for m in mappings]
