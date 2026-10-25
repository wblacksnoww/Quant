"""用户反馈。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["FeedbackIn", "FeedbackOut", "FeedbackStatusUpdate"]


class FeedbackIn(BaseModel):
    category: Literal["suggestion", "bug", "other"] = "suggestion"
    content: str = Field(min_length=1, max_length=2000)


class FeedbackOut(BaseModel):
    id: int
    user_id: int | None = None
    username: str = ""
    category: str = "suggestion"
    content: str
    status: str = "new"
    created_at: str | None = None
    updated_at: str | None = None


class FeedbackStatusUpdate(BaseModel):
    status: Literal["new", "read", "resolved"]
