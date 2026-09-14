from typing import Any, cast

from fastapi import FastAPI, HTTPException, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.correlation import get_correlation_id
from app.domain.asset import (
    AssetCompletionConflict,
    AssetConflict,
    AssetCorrupt,
    AssetDomainError,
    AssetIdempotencyConflict,
    AssetNotFound,
    AssetNotVerified,
    AssetReplacementNotAllowed,
    AssetSizeLimitExceeded,
    AssetUnsupportedMediaType,
    AssetValidationFailed,
    StorageObjectNotFound,
    StorageUnavailable,
)


def problem_response(
    status_code: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    trace_id: str | None = None,
) -> JSONResponse:
    if trace_id is None:
        trace_id = get_correlation_id()
    content: dict[str, Any] = {
        "type": f"https://docs.virtujudge.org/problems/{code.replace('_', '-')}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
        "code": code,
    }
    if trace_id is not None:
        content["trace_id"] = trace_id
    headers = {}
    if trace_id is not None:
        headers["X-Correlation-Id"] = trace_id
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
        media_type="application/problem+json",
    )


def handle_asset_error(err: AssetDomainError, path: str) -> JSONResponse:
    if isinstance(err, AssetNotFound):
        return problem_response(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            "Resource not found",
            "The requested resource was not found.",
            path,
        )
    if isinstance(err, AssetUnsupportedMediaType):
        return problem_response(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            "Unsupported media type",
            "The file type or extension is not supported.",
            path,
        )
    if isinstance(err, AssetSizeLimitExceeded):
        return problem_response(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "payload_too_large",
            "Payload too large",
            "The file size exceeds the allowed limit.",
            path,
        )
    if isinstance(err, AssetCorrupt):
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            err.reason,
            "File validation failed",
            f"The uploaded document failed verification ({err.reason}).",
            path,
        )
    if isinstance(err, StorageObjectNotFound):
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "object_not_found_in_storage",
            "Object not found in storage",
            "The object was not found in storage.",
            path,
        )
    if isinstance(err, AssetValidationFailed):
        return problem_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            err.reason,
            "Validation failed",
            "The request parameters failed validation.",
            path,
        )
    if isinstance(err, AssetIdempotencyConflict):
        return problem_response(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "Idempotency conflict",
            "The idempotency key has already been used with different request parameters.",
            path,
        )
    if isinstance(err, AssetReplacementNotAllowed):
        return problem_response(
            status.HTTP_409_CONFLICT,
            "replacement_not_allowed",
            "Replacement not allowed",
            "Only supporting documents accept replacement.",
            path,
        )
    if isinstance(err, AssetNotVerified):
        return problem_response(
            status.HTTP_409_CONFLICT,
            "asset_not_verified",
            "Asset not verified",
            "Download intent is only available for verified assets.",
            path,
        )
    if isinstance(err, AssetCompletionConflict):
        return problem_response(
            status.HTTP_409_CONFLICT,
            "completion_conflict",
            "Completion conflict",
            "The asset completion conflicts with existing verified or rejected state.",
            path,
        )
    if isinstance(err, AssetConflict):
        return problem_response(
            status.HTTP_409_CONFLICT,
            "conflict",
            "Resource conflict",
            "The requested operation conflicts with the current resource state.",
            path,
        )
    if isinstance(err, StorageUnavailable):
        return problem_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "Service temporarily unavailable",
            "The requested operation could not be completed at this time.",
            path,
        )
    return problem_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
        "An unexpected error occurred.",
        path,
    )


async def asset_request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if (
        "/assets" in request.url.path
        or "/practice-sessions" in request.url.path
        or "/internal" in request.url.path
        or "/ai-jobs" in request.url.path
    ):
        trace_id = get_correlation_id() or getattr(request.state, "correlation_id", None)
        content: dict[str, Any] = {
            "type": "https://docs.virtujudge.org/problems/validation-failed",
            "title": "Validation failed",
            "status": 422,
            "detail": "The request parameters failed validation.",
            "instance": request.url.path,
            "code": "validation_failed",
        }
        if trace_id is not None:
            content["trace_id"] = trace_id
        headers = {}
        if trace_id is not None:
            headers["X-Correlation-Id"] = trace_id
        return JSONResponse(
            status_code=422,
            content=content,
            headers=headers,
            media_type="application/problem+json",
        )
    return await request_validation_exception_handler(request, exc)


async def http_exception_handler(
    request: Request, exc: HTTPException | StarletteHTTPException
) -> JSONResponse:
    trace_id = get_correlation_id() or getattr(request.state, "correlation_id", None)
    status_code = exc.status_code
    if status_code == 401 and exc.detail == "invalid_token":
        code = "invalid_token"
        title = "Invalid token"
    elif status_code == 401:
        code = "unauthorized"
        title = "Unauthorized"
    elif status_code == 403:
        code = "forbidden"
        title = "Forbidden"
    elif status_code == 404:
        code = "not_found"
        title = "Resource not found"
    else:
        code = f"http_{status_code}"
        title = "HTTP error"

    detail = str(exc.detail) if exc.detail else title
    content: dict[str, Any] = {
        "type": f"https://docs.virtujudge.org/problems/{code.replace('_', '-')}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
    }
    if trace_id is not None:
        content["trace_id"] = trace_id
    headers = dict(exc.headers or {})
    if trace_id is not None:
        headers["X-Correlation-Id"] = trace_id
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
        media_type="application/problem+json",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, cast(Any, asset_request_validation_handler))
    app.add_exception_handler(HTTPException, cast(Any, http_exception_handler))
    app.add_exception_handler(StarletteHTTPException, cast(Any, http_exception_handler))
