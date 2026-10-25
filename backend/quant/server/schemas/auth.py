"""认证与账号：登录、令牌、2FA、会话、注册。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "CaptchaOut",
    "ChangePasswordRequest",
    "LoginRequest",
    "RefreshRequest",
    "RegisterRequest",
    "SessionOut",
    "TokenResponse",
    "TotpDisableRequest",
    "TotpEnableRequest",
    "TotpSetupResponse",
    "UserApprovalRequest",
    "UserOut",
]


class UserOut(BaseModel):
    """对外暴露的用户信息，绝不含密码哈希。"""

    id: int
    username: str
    role: str
    is_active: bool
    approval_status: str = "approved"
    totp_enabled: bool
    last_login_at: str | None = None
    created_at: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    totp_code: str | None = Field(default=None, max_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]
    issuer: str


class TotpEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TotpDisableRequest(BaseModel):
    password: str
    code: str


class SessionOut(BaseModel):
    id: int
    jti: str
    ip: str
    user_agent: str
    created_at: str
    expires_at: str


class CaptchaOut(BaseModel):
    token: str
    image: str
    expires_in: int = 300


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    captcha_token: str = Field(min_length=10, max_length=4096)
    captcha_answer: str = Field(min_length=1, max_length=8)


class UserApprovalRequest(BaseModel):
    action: Literal["approve", "reject"]
