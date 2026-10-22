"""双均线 + 新闻/政策过滤：演示“信息面因子 + 统一退出决策”。

买入：金叉 + 新闻情绪/政策风险门槛（可配置为必须存在新闻）。
卖出：止损、止盈、政策风险、负面情绪、死叉，全部通过 ``apply_exit`` 收敛成
唯一动作，并携带结构化退出原因。
"""

from __future__ import annotations

import pandas as pd

from ...core.types import Bar
from ..base import ParamSpec, Strategy
from ..builtin.dual_ma import DualMaStrategy
from ..exits import ExitDecision, ExitReason
from ..indicators import crossover, sma
from ..registry import register_strategy


@register_strategy
class NewsDualMaStrategy(Strategy):
    strategy_id = "news_dual_ma"
    display_name = "双均线 + 新闻政策"
    category = "trend"
    description = (
        "双均线金叉入场，叠加新闻/政策因子过滤：政策风险过高或情绪转负时退出。"
        "需要先在「新闻」页录入带情绪与政策风险的历史/实时新闻。"
    )

    param_specs = DualMaStrategy.param_specs + (
        ParamSpec(
            "news_window",
            "choice",
            "1h",
            choices=("15m", "1h", "4h", "1d"),
            description="新闻因子聚合窗口",
        ),
        ParamSpec("min_confidence", "float", 0.0, 0.0, 1.0, 0.05, description="入场所需最低新闻置信度"),
        ParamSpec("min_sentiment", "float", -1.0, -1.0, 1.0, 0.05, description="入场最低情绪（-1~1）"),
        ParamSpec("max_policy_risk", "float", 1.0, 0.0, 1.0, 0.05, description="入场允许的最高政策风险"),
        ParamSpec("exit_policy_risk", "float", 1.0, 0.0, 1.0, 0.05, description="触发清仓的政策风险阈值"),
        ParamSpec("exit_sentiment", "float", -1.0, -1.0, 1.0, 0.05, description="触发清仓的情绪阈值"),
        ParamSpec("require_news", "bool", False, description="没有新闻时是否禁止入场"),
        ParamSpec("news_half_life", "float", 24.0, 1.0, 168.0, 1.0, description="新闻衰减半衰期（小时）"),
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
        signal = float(crossover(fast_ma, slow_ma).iloc[-1])
        pos = self.position(bar.symbol)
        if self.has_open_order(bar.symbol):
            return

        news = self.news(bar.symbol, str(self.params["news_window"]))

        # --- 多个退出规则：止损 / 止盈 / 政策 / 情绪 / 死叉 --------------- #
        decisions: list[ExitDecision] = []
        if not pos.is_flat and pos.available > 0:
            cost = pos.avg_cost
            if cost > 0:
                pnl = bar.close / cost - 1.0
                if self.params["stop_loss"] > 0 and pnl <= -self.params["stop_loss"]:
                    self.log(f"{bar.symbol} 触发止损 {pnl:.2%}")
                    decisions.append(ExitDecision("close", ExitReason.STOP_LOSS, priority=100))
                if self.params["take_profit"] > 0 and pnl >= self.params["take_profit"]:
                    self.log(f"{bar.symbol} 触发止盈 {pnl:.2%}")
                    decisions.append(ExitDecision("close", ExitReason.TAKE_PROFIT, priority=80))

            if news.event_count > 0 and news.confidence > 0:
                if news.policy_risk >= float(self.params["exit_policy_risk"]):
                    self.log(f"{bar.symbol} 政策风险 {news.policy_risk:.2f} 触发退出")
                    decisions.append(
                        ExitDecision(
                            "close",
                            ExitReason.POLICY_RISK,
                            priority=95,
                            detail={"policy_risk": news.policy_risk},
                        )
                    )
                if news.sentiment <= float(self.params["exit_sentiment"]):
                    self.log(f"{bar.symbol} 新闻情绪 {news.sentiment:.2f} 触发退出")
                    decisions.append(
                        ExitDecision(
                            "close",
                            ExitReason.NEWS_RISK,
                            priority=70,
                            detail={"sentiment": news.sentiment},
                        )
                    )

        if signal < 0 and not pos.is_flat:
            decisions.append(ExitDecision("close", ExitReason.SIGNAL, priority=50))
        if decisions:
            self.apply_exit(bar.symbol, decisions)
            return

        # --- 入场：技术信号 + 新闻/政策门槛 ------------------------------ #
        if signal <= 0 or not pos.is_flat:
            return
        if bool(self.params["require_news"]) and news.event_count == 0:
            return
        if news.event_count > 0:
            if news.confidence < float(self.params["min_confidence"]):
                return
            if news.sentiment < float(self.params["min_sentiment"]):
                return
            if news.policy_risk > float(self.params["max_policy_risk"]):
                return
        self.buy(
            bar.symbol,
            percent=float(self.params["position_pct"]),
            tag="golden_cross",
        )


__all__ = ["NewsDualMaStrategy"]
