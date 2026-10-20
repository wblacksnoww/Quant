"""合成数据源：确定性几何布朗运动 + 波动率聚集。

用途只有两个，但都很重要：

1. **单元测试**：不碰网络、结果可复现（同一 seed 必得同一序列）。
2. **首次启动演示**：容器起来就能点开回测页面看到图，不必先等 akshare。

数据带趋势切换（牛/震荡/熊），让均线类策略有东西可做。
"""

from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from ..base import DataProvider, normalize_frame, register_provider


def _seed_for(symbol: str) -> int:
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


@register_provider("synthetic")
class SyntheticProvider(DataProvider):
    """确定性行情生成器。

    :param start_price: 起始价
    :param annual_drift: 年化漂移
    :param annual_vol: 年化波动率
    :param regime_switch: 是否启用牛/熊/震荡切换
    """

    def __init__(
        self,
        start_price: float = 20.0,
        annual_drift: float = 0.08,
        annual_vol: float = 0.30,
        regime_switch: bool = True,
    ) -> None:
        self.start_price = start_price
        self.annual_drift = annual_drift
        self.annual_vol = annual_vol
        self.regime_switch = regime_switch

    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        rng = np.random.default_rng(_seed_for(str(symbol)))
        days = pd.bdate_range(start=start, end=end)
        n = len(days)
        if n == 0:
            raise ValueError("日期区间为空")

        # 波动率聚集：GARCH(1,1) 的简化版
        base_vol = self.annual_vol / np.sqrt(252)
        shocks = rng.standard_normal(n)
        vol = np.empty(n)
        vol[0] = base_vol
        for i in range(1, n):
            vol[i] = np.sqrt(0.85 * vol[i - 1] ** 2 + 0.15 * (base_vol * (1 + 0.6 * abs(shocks[i - 1]))) ** 2)

        # 趋势切换：每 60~120 个交易日换一次状态
        drift = np.full(n, self.annual_drift / 252)
        if self.regime_switch:
            i = 0
            while i < n:
                length = int(rng.integers(60, 121))
                state = rng.choice([-1.0, 0.0, 1.0], p=[0.25, 0.40, 0.35])
                drift[i : i + length] = state * rng.uniform(0.15, 0.55) / 252
                i += length

        log_returns = drift + vol * shocks
        close = self.start_price * np.exp(np.cumsum(log_returns))

        intraday = np.abs(rng.standard_normal(n)) * vol * 0.8
        open_ = close * np.exp(-0.3 * log_returns + 0.1 * intraday * rng.standard_normal(n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.standard_normal(n)) * vol * 0.5)
        low = np.minimum(open_, close) * (1 - np.abs(rng.standard_normal(n)) * vol * 0.5)
        volume = (rng.lognormal(mean=15.0, sigma=0.5, size=n) * (1 + 3 * np.abs(log_returns) / vol)).astype(
            int
        )
        amount = volume * close

        frame = pd.DataFrame(
            {
                "date": days,
                "open": np.round(open_, 2),
                "high": np.round(high, 2),
                "low": np.round(low, 2),
                "close": np.round(close, 2),
                "volume": volume,
                "amount": np.round(amount, 2),
            }
        )
        # 保证 low <= open/close <= high
        frame["high"] = frame[["open", "close", "high"]].max(axis=1)
        frame["low"] = frame[["open", "close", "low"]].min(axis=1)
        return normalize_frame(frame, symbol=str(symbol))

    def realtime(self, symbols):
        return {}


@register_provider("synthetic_volatile")
class VolatileSyntheticProvider(SyntheticProvider):
    """高波动版本，用于压测风控与止损逻辑。"""

    def __init__(self) -> None:
        super().__init__(annual_drift=0.12, annual_vol=0.65)


__all__ = ["SyntheticProvider", "VolatileSyntheticProvider"]
