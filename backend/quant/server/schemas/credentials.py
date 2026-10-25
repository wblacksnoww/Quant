"""外部凭据（broker / datasource）。密钥字段永不出现在响应里。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = ["CredentialCreate", "CredentialOut"]


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["broker", "datasource"]
    payload: dict[str, Any] = Field(min_length=1)
    description: str = Field(default="", max_length=255)


class CredentialOut(BaseModel):
    id: int
    name: str
    kind: str
    description: str
    created_at: str | None = None
    updated_at: str | None = None
