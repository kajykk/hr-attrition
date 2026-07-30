"""认证 schemas（参考 D05 3.1）."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    totp_code: Optional[str] = Field(default=None, max_length=6, description="2FA 验证码（管理员强制）")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int = Field(description="access token 有效期（秒）")
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class UserOut(BaseModel):
    id: UUID
    name: str
    role: str
    tenant_id: UUID
    email: str

    model_config = {"from_attributes": True}


class LoginResult(BaseModel):
    """登录返回体（D05 3.1）."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: UserOut
