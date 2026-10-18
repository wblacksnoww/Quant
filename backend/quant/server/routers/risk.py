"""风控路由：规则目录 + 预设。

前端下拉"保守/均衡/激进"只是选一套阈值，真正的执行永远在引擎侧。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...risk.base import DEFAULT_RULES, RiskManager
from ...storage.models import User
from ..deps import get_current_user
from ..schemas import RiskRuleOut

router = APIRouter(prefix="/risk", tags=["风控"])

PRESETS: dict[str, list[dict]] = {
    "conservative": [
        {"id": "lot_size", "enabled": True},
        {"id": "max_position_pct", "enabled": True, "limit": 0.15},
        {"id": "max_order_value_pct", "enabled": True, "limit": 0.10},
        {"id": "max_positions", "enabled": True, "limit": 5},
        {"id": "daily_loss_limit", "enabled": True, "limit": 0.03},
        {"id": "max_drawdown", "enabled": True, "limit": 0.10},
        {"id": "order_rate_limit", "enabled": True, "max_orders": 30, "window_seconds": 60},
        {"id": "blacklist", "enabled": True, "symbols": []},
    ],
    "balanced": DEFAULT_RULES,
    "aggressive": [
        {"id": "lot_size", "enabled": True},
        {"id": "max_position_pct", "enabled": True, "limit": 0.98},
        {"id": "max_order_value_pct", "enabled": True, "limit": 0.98},
        {"id": "max_positions", "enabled": True, "limit": 30},
        {"id": "daily_loss_limit", "enabled": True, "limit": 0.15},
        {"id": "max_drawdown", "enabled": True, "limit": 0.50},
        {"id": "order_rate_limit", "enabled": True, "max_orders": 300, "window_seconds": 60},
        {"id": "blacklist", "enabled": True, "symbols": []},
    ],
}


@router.get("/rules", response_model=list[RiskRuleOut], summary="默认风控规则")
def get_rules(_: User = Depends(get_current_user)) -> list[RiskRuleOut]:
    manager = RiskManager.from_config(None)
    return [RiskRuleOut(**schema) for schema in manager.schema()]


@router.get("/presets", response_model=dict, summary="风控预设")
def get_presets(_: User = Depends(get_current_user)) -> dict:
    return {"presets": PRESETS, "default": "balanced"}


__all__ = ["PRESETS", "router"]
