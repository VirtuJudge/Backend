from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.erasure import get_erasure_workflow
from app.api.errors import problem_response
from app.api.schemas.erasure import ErasureResponse
from app.application.erasure_workflow import ErasureWorkflow
from app.domain.erasure import ErasureConflict, ErasureForbidden, ErasureNotFound
from app.domain.user import User

router = APIRouter(prefix="/api/v1", tags=["Erasure"])


def erasure_error(error: Exception, request: Request) -> JSONResponse:
    if isinstance(error, ErasureNotFound):
        return problem_response(
            404, "not_found", "Resource not found", "Resource not found", request.url.path
        )
    if isinstance(error, ErasureForbidden):
        return problem_response(
            403, "forbidden", "Forbidden", "Team Owner permission is required", request.url.path
        )
    if isinstance(error, ErasureConflict):
        return problem_response(
            409,
            str(error),
            "Erasure conflict",
            "Deletion conflicts with the current request",
            request.url.path,
        )
    raise error


@router.get("/erasure-requests/{request_id}", response_model=ErasureResponse)
async def get_erasure_request(
    request_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    workflow: ErasureWorkflow = Depends(get_erasure_workflow),
) -> ErasureResponse | JSONResponse:
    try:
        return ErasureResponse.model_validate(await workflow.get(request_id, current_user.id))
    except (ErasureNotFound, ErasureForbidden, ErasureConflict) as error:
        return erasure_error(error, request)
