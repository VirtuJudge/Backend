from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_current_user
from app.api.schemas.user import UserResponse
from app.domain.user import User

router = APIRouter(
    prefix="/api/v1",
)


@router.get("/me", response_model=UserResponse, tags=["users"])
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        username=current_user.subject,
        email=current_user.email,
        created_at=current_user.created_at.isoformat(),
    )
