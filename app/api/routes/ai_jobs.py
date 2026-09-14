from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.services import get_ai_jobs
from app.api.dependencies.worker_auth import AuthenticatedWorker, require_worker_auth
from app.api.schemas.ai_jobs import AIJobStatusResponse
from app.application.ai_jobs import AIJobs

router = APIRouter(prefix="/internal/v1", tags=["Internal AI Jobs"])


@router.get(
    "/ai-jobs/{job_id}",
    response_model=AIJobStatusResponse,
    operation_id="get_internal_ai_job_status",
    responses={
        401: {"description": "Unauthorized worker credentials"},
        404: {"description": "AI job not found"},
    },
)
async def get_internal_ai_job_status(
    job_id: UUID,
    worker: AuthenticatedWorker = Depends(require_worker_auth),
    ai_jobs: AIJobs = Depends(get_ai_jobs),
) -> AIJobStatusResponse:
    job = await ai_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI job not found",
        )
    return AIJobStatusResponse(
        id=job.id,
        job_id=job.id,
        status=job.status,
        last_update_sequence=job.last_update_sequence,
        cancel_requested=job.cancel_requested,
    )
