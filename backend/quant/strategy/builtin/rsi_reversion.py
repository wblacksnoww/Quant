"""RSI 均值回归策略：超卖买入、超买卖出。"""

from __future__ import annotations

from ...core.types import Bar
from ..base import ParamSpec, Strategy
from ..indicators import rsi
from ..registry import register_strategy


@register_strategy
class RsiReversionStrategy(Strategy):
    strategy_id = "rsi_reversion"
    display_name = "RSI 均值回归"
    category = "mean_reversion"
    description = "RSI 跌破超卖线买入，回到超买线卖出。震荡市表现优于趋势市，单边下跌行情需要靠止损控制回撤。"

    param_specs = (
        ParamSpec("period", "int", 14, 2, 60, description="RSI 周期"),
        ParamSpec("oversold", "float", 30.0, 5.0, 45.0, 1.0, description="超卖阈值"),
        ParamSpec("overbought", "float", 70.0, 55.0, 95.0, 1.0, description="超买阈值"),
        ParamSpec("position_pct", "float", 0.9, 0.05, 1.0, 0.05, description="目标仓位"),
        ParamSpec("stop_loss", "float", 0.10, 0.0, 0.5, 0.01, description="止损比例，0=不启用"),
    )

    def on_bar(self, bar: Bar) -> None:
        period = int(self.params["period"])
        frame = self.history(bar.symbol)
        if len(frame) < period + 5:
            return

        values = rsi(frame["close"], period)
        current = values.iloc[-1]
        if current != current:  # NaN
            return

        pos = self.position(bar.symbol)
        if self.has_open_order(bar.symbol):
            return

        if not pos.is_flat and pos.available > 0:
            cost = pos.avg_cost
            if cost > 0 and self.params["stop_loss"] > 0:
                if bar.close / cost - 1.0 <= -float(self.params["stop_loss"]):
                    self.close(bar.symbol, tag="stop_loss")
                    return

        if current <= float(self.params["oversold"]) and pos.is_flat:
            self.log(f"{bar.symbol} RSI={current:.1f} 超卖，买入")
            self.buy(bar.symbol, percent=float(self.params["position_pct"]), tag="rsi_oversold")
        elif current >= float(self.params["overbought"]) and not pos.is_flat:
            self.log(f"{bar.symbol} RSI={current:.1f} 超买，卖出")
            self.close(bar.symbol, tag="rsi_overbought")


__all__ = ["RsiReversionStrategy"]
