"""双均线 + 新闻/政策过滤：演示"信息面因子 + 统一退出决策"。

## 为什么是 spec 驱动

这个策略**不再自己算指标**。它把参数翻译成一份 spec v1，交给 C++
``SpecEvaluator`` 求值 —— 与拼装策略、与交易网关共用同一份解释器。

新闻因子通过 ``extras`` 列传入 C++（``Columns::at()`` 原生支持任意字段名），
所以"技术信号 + 新闻门槛"这类组合不需要 C++ 侧新增策略类型。

这样做的收益：回测 / 纸盘 / 实盘三处跑的是**同一段求值代码**，
不会出现"回测里新闻过滤生效、实盘里失效"这种无声漂移。

## 逻辑

* 入场：快慢均线金叉，且通过新闻门槛（置信度 / 情绪 / 政策风险）；
* 出场：止损、止盈、政策风险、负面情绪、死叉 —— 收敛成唯一动作并带结构化原因。
"""

from __future__ import annotations

from typing import Any

from ..base import ParamSpec, Strategy
from ..builtin.dual_ma import DualMaStrategy
from ..registry import register_strategy

STRATEGY_ID = "news_dual_ma"


def build_spec(
    *,
    fast: int = 10,
    slow: int = 30,
    position_pct: float = 0.95,
    stop_loss: float = 0.08,
    take_profit: float = 0.0,
    news_window: str = "1h",
    min_confidence: float = 0.0,
    min_sentiment: float = -1.0,
    max_policy_risk: float = 1.0,
    require_news: bool = False,
) -> dict[str, Any]:
    """把策略参数翻译成 spec v1（C++ 可直接编译的那种）。

    新闻字段名与聚合窗口绑定（见 ``custom_fields.NEWS_FIELDS``）：
    ``news_sentiment`` 是 1h 窗口，``news_sentiment_1d`` 是 1d 窗口。
    这里按 ``news_window`` 选择对应后缀。
    """
    suffix = "" if news_window == "1h" else "_1d"
    sentiment_field = f"news_sentiment{suffix}"
    confidence_field = f"news_confidence{suffix}"
    policy_field = f"news_policy_risk{suffix}"
    count_field = f"news_event_count{suffix}"

    sma_fast = {"kind": "indicator", "name": "sma", "params": {"period": int(fast)}}
    sma_slow = {"kind": "indicator", "name": "sma", "params": {"period": int(slow)}}

    entry: list[dict[str, Any]] = [
        {"type": "cross_above", "left": sma_fast, "right": sma_slow},
    ]
    # 新闻条件始终显式写入 spec —— 即使取默认值。
    # 早期实现只在"参数比默认更严"时才加条件，结果 min_sentiment=-1.0（默认）
    # 完全不产生新闻条件，spec 退化成纯双均线，新闻数据也就不必物化
    # （factor() 零调用）。那会让"新闻策略"名不副实，且掩盖了新闻链路故障。
    entry.append(
        {
            "type": "gte",
            "left": {"kind": "field", "field": sentiment_field},
            "right": {"kind": "const", "value": float(min_sentiment)},
        }
    )
    entry.append(
        {
            "type": "gte",
            "left": {"kind": "field", "field": confidence_field},
            "right": {"kind": "const", "value": float(min_confidence)},
        }
    )
    if require_news:
        entry.append(
            {
                "type": "gt",
                "left": {"kind": "field", "field": count_field},
                "right": {"kind": "const", "value": 0.0},
            }
        )
    if max_policy_risk < 1.0:
        entry.append(
            {
                "type": "lte",
                "left": {"kind": "field", "field": policy_field},
                "right": {"kind": "const", "value": float(max_policy_risk)},
            }
        )

    return {
        "version": 1,
        "description": f"双均线 {fast}/{slow} + 新闻过滤（{news_window}）",
        "category": "trend",
        "entry": entry,
        "exit": [{"type": "cross_below", "left": sma_fast, "right": sma_slow}],
        "position_pct": float(position_pct),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
    }


@register_strategy
class NewsDualMaStrategy(Strategy):
    strategy_id = STRATEGY_ID
    display_name = "双均线 + 新闻政策"
    category = "trend"
    description = (
        "双均线金叉入场，叠加新闻/政策因子过滤：政策风险过高或情绪转负时退出。"
        "需要先在「新闻」页录入带情绪与政策风险的历史/实时新闻。"
        "信号由 C++ 解释器求值，与拼装策略、交易网关共用同一份实现。"
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
        # 把参数翻译成 spec，后续信号求值全部交给 C++
        self.spec = build_spec(
            fast=int(self.params["fast"]),
            slow=int(self.params["slow"]),
            position_pct=float(self.params["position_pct"]),
            stop_loss=float(self.params["stop_loss"]),
            take_profit=float(self.params["take_profit"]),
            news_window=str(self.params["news_window"]),
            min_confidence=float(self.params["min_confidence"]),
            min_sentiment=float(self.params["min_sentiment"]),
            max_policy_risk=float(self.params["max_policy_risk"]),
            require_news=bool(self.params["require_news"]),
        )


__all__ = ["NewsDualMaStrategy", "build_spec"]
