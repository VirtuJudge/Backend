from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.application.health import get_health_status

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    result = get_health_status()
    return HealthResponse(status=result.status)
