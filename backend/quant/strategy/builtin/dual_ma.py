"""双均线趋势策略：金叉开仓、死叉清仓，带可选止损。"""

from __future__ import annotations

import pandas as pd

from ...core.types import Bar
from ..base import ParamSpec, Strategy
from ..exits import ExitDecision, ExitReason
from ..indicators import crossover, sma
from ..registry import register_strategy


@register_strategy
class DualMaStrategy(Strategy):
    strategy_id = "dual_ma"
    display_name = "双均线趋势"
    category = "trend"
    description = "快慢均线金叉买入、死叉卖出。经典趋势跟随，震荡市会被反复打脸，适合配合止损与仓位控制使用。"

    param_specs = (
        ParamSpec("fast", "int", 10, 2, 120, description="快线周期（日）"),
        ParamSpec("slow", "int", 30, 5, 250, description="慢线周期（日）"),
        ParamSpec("position_pct", "float", 0.95, 0.05, 1.0, 0.05, description="目标仓位（占权益）"),
        ParamSpec("stop_loss", "float", 0.08, 0.0, 0.5, 0.01, description="止损比例，0=不启用"),
        ParamSpec("take_profit", "float", 0.0, 0.0, 3.0, 0.05, description="止盈比例，0=不启用"),
    )

    def __init__(self, ctx, params=None):
        super().__init__(ctx, params)
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("fast 必须小于 slow")

    def on_bar(self, bar: Bar) -> None:
        frame = self.history(bar.symbol)
        need = int(self.params["slow"]) + 2
        if len(frame) < need:
            return

        close: pd.Series = frame["close"]
        fast_ma = sma(close, int(self.params["fast"]))
        slow_ma = sma(close, int(self.params["slow"]))
        cross = crossover(fast_ma, slow_ma)
        signal = float(cross.iloc[-1])

        pos = self.position(bar.symbol)
        if self.has_open_order(bar.symbol):
            return

        # --- 持仓管理：多条退出规则 → 一个执行动作 ---------------------- #
        decisions: list[ExitDecision] = []
        if not pos.is_flat and pos.available > 0:
            cost = pos.avg_cost
            if cost > 0:
                pnl = bar.close / cost - 1.0
                if self.params["stop_loss"] > 0 and pnl <= -self.params["stop_loss"]:
                    self.log(f"{bar.symbol} 触发止损 {pnl:.2%}")
                    decisions.append(
                        ExitDecision("close", ExitReason.STOP_LOSS, priority=100, detail={"pnl": pnl})
                    )
                if self.params["take_profit"] > 0 and pnl >= self.params["take_profit"]:
                    self.log(f"{bar.symbol} 触发止盈 {pnl:.2%}")
                    decisions.append(
                        ExitDecision("close", ExitReason.TAKE_PROFIT, priority=80, detail={"pnl": pnl})
                    )
        if signal < 0 and not pos.is_flat:
            decisions.append(ExitDecision("close", ExitReason.SIGNAL, priority=50))
        if decisions:
            self.apply_exit(bar.symbol, decisions)
            return

        # --- 入场 -------------------------------------------------------- #
        if signal > 0 and pos.is_flat:
            self.buy(
                bar.symbol,
                percent=float(self.params["position_pct"]),
                tag="golden_cross",
            )


__all__ = ["DualMaStrategy"]
