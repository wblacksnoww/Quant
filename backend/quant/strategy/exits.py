"""统一的退出决策：多个退出理由 → 一个执行动作。

策略可以同时维护止损、止盈、信号退出、新闻/政策风险等规则；每条规则只
产生一个 :class:`ExitDecision`，由 :func:`pick_exit` 按优先级选出唯一动作，
最终统一走 ``Strategy.apply_exit`` → ``close/sell``。这样不会因为新增一个
影响因素就增加一个卖出接口，也不会重复下单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ExitReason(str, Enum):
    """退出原因（落库到订单 tag，用于审计与归因）。"""

    SIGNAL = "signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    NEWS_RISK = "news_risk"
    POLICY_RISK = "policy_risk"
    TIME_EXIT = "time_exit"
    RISK_LIMIT = "risk_limit"
    REBALANCE = "rebalance"
    MANUAL = "manual"


@dataclass
class ExitDecision:
    """一条退出规则的结果；``action="hold"`` 表示不动作。"""

    action: Literal["close", "reduce", "hold"]
    reason: ExitReason | str
    fraction: float = 1.0
    priority: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


def pick_exit(decisions: list[ExitDecision | None]) -> ExitDecision | None:
    """按优先级选唯一动作；同优先级优先清仓、再优先更大减仓比例。"""
    active = [item for item in decisions if item is not None and item.action != "hold" and item.fraction > 0]
    if not active:
        return None
    return max(
        active,
        key=lambda item: (
            item.priority,
            1 if item.action == "close" else 0,
            min(1.0, max(0.0, item.fraction)),
        ),
    )


__all__ = ["ExitDecision", "ExitReason", "pick_exit"]
