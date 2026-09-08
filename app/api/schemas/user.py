from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    email: EmailStr
    password: str = Field(min_length=8)


class UpdateUserRequest(BaseModel):
    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=30,
    )
    email: EmailStr | None = None


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: EmailStr | None = None
    created_at: str
    display_name: str | None = None
