"""C++ 交易网关：tick 级策略的启动参数与运行状态。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .runs import RiskRuleInput

__all__ = ["GatewayStartRequest", "GatewayStatusOut"]


class GatewayStartRequest(BaseModel):
    strategy_type: Literal["grid", "breakout", "tick_momentum", "bar_spec"]
    params: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(min_length=1, max_length=10)
    bar_interval_sec: int = Field(default=60, ge=1, le=86400)
    sandbox: bool = True
    confirm_live: bool = False
    credential_id: int | None = None
    risk: list[RiskRuleInput] | None = None


class GatewayStatusOut(BaseModel):
    running: bool = False
    mode: str | None = None
    strategy: str | None = None
    symbols: list[str] = Field(default_factory=list)
    bar_interval_sec: int | None = None
    positions: dict[str, Any] = Field(default_factory=dict)
    strategy_running: bool = False
    stats: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
