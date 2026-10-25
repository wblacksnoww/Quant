"""新闻与政策因子：录入、查询、因子聚合、抓取状态。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["NewsFactorOut", "NewsFetchStatusOut", "NewsItemIn", "NewsItemOut"]


class NewsItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = ""
    url: str = ""
    symbols: list[str] = Field(default_factory=list)
    market: str = ""
    event_type: str = "other"
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    policy_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    published_at: datetime | None = None
    effective_at: datetime | None = None


class NewsItemOut(BaseModel):
    id: int
    source: str = "manual"
    external_id: str | None = None
    title: str
    summary: str = ""
    url: str = ""
    published_at: str | None = None
    ingested_at: str | None = None
    effective_at: str | None = None
    symbols: list[str] = Field(default_factory=list)
    market: str = ""
    event_type: str = "other"
    sentiment: float = 0.0
    policy_risk: float = 0.0
    confidence: float = 0.0
    created_by: int | None = None
    created_at: str | None = None


class NewsFactorOut(BaseModel):
    symbol: str
    as_of: str
    window: str
    sentiment: float = 0.0
    policy_risk: float = 0.0
    confidence: float = 0.0
    event_count: int = 0
    items: list[NewsItemOut] = Field(default_factory=list)


class NewsFetchStatusOut(BaseModel):
    enabled: bool = False
    interval_seconds: float = 120.0
    feeds: list[str] = Field(default_factory=list)
    last_run_at: str | None = None
    last_result: dict[str, Any] | None = None
