import uuid

from pydantic import BaseModel, Field

from app.models import Role


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    login: str
    full_name: str
    role: Role
    service_code: str | None
    must_change_password: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - not a secret
    expires_in: int
    user: UserOut


class MessageResponse(BaseModel):
    message: str
