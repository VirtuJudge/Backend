from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_project_service
from app.api.schemas.project import (
    ErasureRequestResponse,
    ProjectCreateRequest,
    ProjectDeleteRequest,
    ProjectPage,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.application.services.project_service import (
    ProjectConfirmationRequired,
    ProjectForbidden,
    ProjectNotFound,
    ProjectPreconditionFailed,
    ProjectService,
    project_etag,
)
from app.domain.user import User

router = APIRouter(
    prefix="/api/v1",
)


def project_response(project: object) -> ProjectResponse:
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.get("/teams/{team_id}/projects", response_model=ProjectPage, tags=["projects"])
async def list_projects(
    team_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
    cursor: UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=150),
    limit: int = Query(default=50, ge=1, le=100),
) -> ProjectPage:
    try:
        projects, next_cursor = await service.list(team_id, current_user.id, cursor, search, limit)
    except ProjectForbidden as error:
        raise HTTPException(status_code=403, detail="team_forbidden") from error
    return ProjectPage(
        items=[project_response(project) for project in projects],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "/teams/{team_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
async def create_project(
    team_id: UUID,
    request: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    try:
        project = await service.create(team_id, current_user.id, request.name, request.description)
    except ProjectForbidden as error:
        raise HTTPException(status_code=403, detail="team_forbidden") from error
    return project_response(project)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    tags=["projects"],
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def get_project(
    project_id: UUID,
    response: Response,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    try:
        project = await service.get(project_id, current_user.id)
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    except ProjectForbidden as error:
        raise HTTPException(status_code=403, detail="project_forbidden") from error
    response.headers["ETag"] = f'"{project_etag(project)}"'
    return project_response(project)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    tags=["projects"],
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Entity tag for optimistic concurrency control",
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def update_project(
    project_id: UUID,
    request: ProjectUpdateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
    if_match: str | None = Header(default=None),
) -> ProjectResponse:
    try:
        project = await service.update(
            project_id,
            current_user.id,
            request.name,
            request.description,
            "description" in request.model_fields_set,
            if_match,
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    except ProjectForbidden as error:
        raise HTTPException(status_code=403, detail="project_forbidden") from error
    except ProjectPreconditionFailed as error:
        raise HTTPException(status_code=412, detail="project_precondition_failed") from error
    response.headers["ETag"] = f'"{project_etag(project)}"'
    return project_response(project)


@router.delete(
    "/projects/{project_id}",
    response_model=ErasureRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["projects"],
)
async def delete_project(
    project_id: UUID,
    request: ProjectDeleteRequest,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
    idempotency_key: str = Header(..., min_length=1, max_length=255),
) -> ErasureRequestResponse:
    try:
        erasure = await service.request_erasure(
            project_id, current_user.id, request.confirmation, idempotency_key
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    except ProjectForbidden as error:
        raise HTTPException(status_code=403, detail="project_forbidden") from error
    except ProjectConfirmationRequired as error:
        raise HTTPException(status_code=409, detail="confirmation_required") from error
    return ErasureRequestResponse(id=erasure.id, project_id=erasure.project_id)
