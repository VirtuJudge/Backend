from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_current_user
from app.domain.user import User

router = APIRouter()


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    return current_user