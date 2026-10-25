"""策略：内置策略元信息、用户自建策略（条件拼装）、分享与派生。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "AdminStrategyOut",
    "AdminStrategyPatch",
    "CustomStrategyIn",
    "CustomStrategyOut",
    "CustomStrategyPatch",
    "ParamOut",
    "SharedStrategyOut",
    "StrategyOut",
    "StrategyShareIn",
    "StrategyShareOut",
]


class ParamOut(BaseModel):
    """策略参数元信息，前端据此自动生成表单控件。"""

    name: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[Any] = Field(default_factory=list)
    description: str = ""


class StrategyOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    params: list[ParamOut]


class CustomStrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=255)
    spec: dict[str, Any]


class CustomStrategyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    spec: dict[str, Any] | None = None
    enabled: bool | None = None


class CustomStrategyOut(BaseModel):
    id: int
    user_id: int
    name: str
    description: str
    category: str
    spec: dict[str, Any]
    enabled: bool
    version: int = 1
    source_strategy_id: int | None = None
    source_version: int | None = None
    forked_at: str | None = None
    update_available: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class SharedStrategyOut(BaseModel):
    share_id: int
    share_type: str
    strategy_id: int
    name: str
    description: str
    category: str
    spec: dict[str, Any] | None = None
    version: int
    owner_username: str
    updated_at: str | None = None
    fork_id: int | None = None
    fork_version: int | None = None
    update_available: bool = False


class StrategyShareIn(BaseModel):
    scope: Literal["public", "user"] = "public"
    target_username: str | None = Field(default=None, max_length=64)


class StrategyShareOut(BaseModel):
    id: int
    scope: str
    target_username: str | None = None
    created_at: str | None = None


class AdminStrategyOut(CustomStrategyOut):
    owner_username: str = ""
    share_count: int = 0
    fork_count: int = 0


class AdminStrategyPatch(BaseModel):
    enabled: bool
