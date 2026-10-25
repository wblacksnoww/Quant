"""系统信息、审计链、数据缓存状态。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["AuditOut", "AuditVerifyOut", "DataCacheOut", "HealthOut", "SystemInfo"]


class HealthOut(BaseModel):
    status: str
    version: str
    env: str
    database: bool
    uptime_seconds: float


class SystemInfo(BaseModel):
    name: str
    version: str
    env: str
    python: str
    database: str
    data_provider: str
    available_providers: list[str]
    encryption: dict[str, Any]
    auth: dict[str, Any]
    paper_running: bool
    strategies: list[str]
    warnings: list[str] = Field(default_factory=list)
    # C++ 加速核心状态：available=False 时回测走 Python 路径（慢 10~70 倍）
    native: dict[str, Any] = Field(default_factory=dict)


class AuditOut(BaseModel):
    id: int
    ts: str
    actor: str
    action: str
    target: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ip: str
    prev_hash: str
    hash: str


class AuditVerifyOut(BaseModel):
    valid: bool
    checked: int
    message: str


class DataCacheOut(BaseModel):
    symbol: str
    code: str = ""
    market: str = "CN"
    rows: int
    start: str | None
    end: str | None
    size_kb: float
