"""回测应用服务：请求 DTO → EngineConfig（路由与 CLI 共用）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.errors import InvalidRequest
from ..engine.base import EngineConfig


def build_engine_config(
    body: Any,
    *,
    user_id: int,
    default_cash: float,
    rf: float,
    params: dict[str, Any] | None = None,
) -> EngineConfig:
    end = body.end or date.today()
    start = body.start or (end - timedelta(days=365 * 3))
    if start > end:
        raise InvalidRequest("开始日期不能晚于结束日期")
    return EngineConfig(
        strategy_id=body.strategy_id,
        symbols=body.symbols,
        start=start,
        end=end,
        initial_cash=body.initial_cash or default_cash,
        params=body.params if params is None else params,
        cost=body.cost.model_dump() if hasattr(body.cost, "model_dump") else dict(body.cost or {}),
        risk=(
            [r.model_dump(exclude_none=True) if hasattr(r, "model_dump") else dict(r) for r in body.risk]
            if body.risk
            else None
        ),
        benchmark=body.benchmark,
        risk_free_rate=body.risk_free_rate or rf,
        bar_interval=getattr(body, "bar_interval", None) or "1d",
        user_id=user_id,
    )


__all__ = ["build_engine_config"]
