"""横截面动量轮动：按 N 日收益率排序，持有最强的 K 只，定期再平衡。

多标的策略的参考实现——它演示了如何用 ``on_bars`` 一次拿到全市场 bar，
以及如何把"调仓"表达成一系列 order_target_percent 调用。
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.types import Bar
from ..base import ParamSpec, Strategy
from ..registry import register_strategy


@register_strategy
class MomentumRotationStrategy(Strategy):
    strategy_id = "momentum_rotation"
    display_name = "动量轮动"
    category = "momentum"
    description = (
        "每 N 个交易日按过去 lookback 日涨幅排序，换入前 top_k 名，"
        "等权持有。适合标的池较大（如宽基成分股、行业 ETF）的场景。"
    )

    param_specs = (
        ParamSpec("lookback", "int", 20, 5, 250, description="动量回看天数"),
        ParamSpec("top_k", "int", 1, 1, 10, description="持有标的数量"),
        ParamSpec("rebalance_days", "int", 5, 1, 120, description="再平衡间隔（交易日）"),
        ParamSpec("position_pct", "float", 0.95, 0.1, 1.0, 0.05, description="总仓位上限"),
        ParamSpec("min_momentum", "float", -1.0, -1.0, 1.0, 0.01, description="最低动量阈值"),
    )

    def __init__(self, ctx, params=None):
        super().__init__(ctx, params)
        self._last_rebalance_day: int | None = None

    def on_bars(self, bars: Mapping[str, Bar]) -> None:
        lookback = int(self.params["lookback"])
        if any(len(self.history(s)) < lookback + 1 for s in bars):
            return

        # 用交易日序号做节流，避免依赖具体日期格式
        day_index = len(self.history(next(iter(bars)))) - 1
        interval = int(self.params["rebalance_days"])
        if self._last_rebalance_day is not None and day_index - self._last_rebalance_day < interval:
            return
        self._last_rebalance_day = day_index

        scores: list[tuple[str, float]] = []
        for symbol in bars:
            frame = self.history(symbol, lookback + 1)
            momentum = frame["close"].iloc[-1] / frame["close"].iloc[0] - 1.0
            scores.append((symbol, float(momentum)))
        scores.sort(key=lambda item: item[1], reverse=True)

        threshold = float(self.params["min_momentum"])
        top = [(s, m) for s, m in scores if m >= threshold][: int(self.params["top_k"])]
        selected = {s for s, _ in top}
        weight = float(self.params["position_pct"]) / max(1, len(top)) if top else 0.0

        # 先卖出掉出榜单的
        for symbol in bars:
            if symbol not in selected and not self.position(symbol).is_flat:
                if not self.has_open_order(symbol):
                    self.close(symbol, tag="rotation_out")

        # 再买入入选的
        for symbol in selected:
            if not self.has_open_order(symbol):
                self.order_target_percent(symbol, weight, tag="rotation_in")


__all__ = ["MomentumRotationStrategy"]
