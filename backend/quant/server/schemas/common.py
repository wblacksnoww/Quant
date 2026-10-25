"""通用响应模型：所有路由都会用到的两个最小结构。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["ErrorBody", "Message"]


class Message(BaseModel):
    """操作成功回执。``detail`` 供前端展示次要信息（如影响的条数）。"""

    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    """统一错误响应体，由全局异常处理器填充。"""

    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
