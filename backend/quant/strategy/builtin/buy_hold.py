"""买入持有基准策略。

回测时用作对照：超额收益、夏普、最大回撤都要跟它比才有意义。
"""

from __future__ import annotations

from typing import Mapping

from ...core.types import Bar
from ..base import ParamSpec, Strategy
from ..registry import register_strategy


@register_strategy
class BuyHoldStrategy(Strategy):
    strategy_id = "buy_hold"
    display_name = "买入持有（基准）"
    category = "benchmark"
    description = "第一个交易日等权买入全部标的并持有到底，作为其它策略的对照基准。"

    param_specs = (
        ParamSpec("rebalance_days", "int", 0, 0, 250, description="再平衡间隔（天），0=不再平衡"),
        ParamSpec("position_pct", "float", 1.0, 0.05, 1.0, 0.05, description="总仓位"),
    )

    def on_bars(self, bars: Mapping[str, Bar]) -> None:
        symbols = list(bars)
        if not symbols:
            return
        weight = 1.0 / len(symbols)
        max_weight = float(self.params.get("position_pct", 1.0)) if self.params.get("position_pct") else 1.0

        for symbol in symbols:
            pos = self.position(symbol)
            if pos.is_flat and not self.has_open_order(symbol):
                self.buy(symbol, percent=min(weight, max_weight) * 0.98, tag="buy_hold")


__all__ = ["BuyHoldStrategy"]
