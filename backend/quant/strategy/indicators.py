"""纯函数式技术指标（基于 pandas，无状态、易测试）。

刻意不引 TA-Lib：它需要编译 C 扩展，在容器里是常见的部署绊脚石。
这些实现的性能对日线级别的回测完全够用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def std(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).std(ddof=0)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI。"""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0).where(avg_loss.notna(), np.nan)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(series: pd.Series, period: int = 20, width: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, period)
    dev = std(series, period)
    return mid - width * dev, mid, mid + width * dev


def crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """金叉 +1、死叉 -1、其它 0。"""
    diff = fast - slow
    prev = diff.shift(1)
    out = pd.Series(0.0, index=diff.index)
    out[(diff > 0) & (prev <= 0)] = 1.0
    out[(diff < 0) & (prev >= 0)] = -1.0
    return out


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    dif = ema(series, fast) - ema(series, slow)
    dea = ema(dif, signal)
    return dif, dea, (dif - dea) * 2.0


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).max()


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).min()


def momentum(series: pd.Series, period: int) -> pd.Series:
    return series.pct_change(period)


__all__ = [
    "atr",
    "bollinger",
    "crossover",
    "ema",
    "macd",
    "momentum",
    "rolling_high",
    "rolling_low",
    "rsi",
    "sma",
    "std",
]
