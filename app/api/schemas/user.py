from pydantic import BaseModel, EmailStr, Field
from uuid import UUID


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
    email: EmailStr
    created_at: str
