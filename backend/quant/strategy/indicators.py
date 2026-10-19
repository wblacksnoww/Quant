"""纯函数式技术指标（基于 pandas，无状态、易测试）。

刻意不引 TA-Lib：它需要编译 C 扩展，在容器里是常见的部署绊脚石。
这些实现的性能对日线级别的回测完全够用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 免代码策略共用指标白名单（v1 条件拼装与 v2 蓝图读写同一张表）
#   id → {参数名: ("int" | "float", 默认值或 None=必填)}
# 边界：period 类参数 2~500；宽度类 0.1~10.0。加指标 = 在 indicators.py 写纯函数
# + 在此登记一行，v1/v2 两处解释器同步可用。
# --------------------------------------------------------------------------- #

INDICATOR_SPECS: dict[str, dict[str, tuple[str, float | None]]] = {
    "sma": {"period": ("int", None)},
    "ema": {"period": ("int", None)},
    "rsi": {"period": ("int", None)},
    "std": {"period": ("int", None)},
    "rolling_high": {"period": ("int", None)},
    "rolling_low": {"period": ("int", None)},
    "momentum": {"period": ("int", None)},
    "atr": {"period": ("int", None)},
    "boll_lower": {"period": ("int", None), "width": ("float", 2.0)},
    "boll_mid": {"period": ("int", None), "width": ("float", 2.0)},
    "boll_upper": {"period": ("int", None), "width": ("float", 2.0)},
    "macd_dif": {"fast": ("int", 12), "slow": ("int", 26), "signal": ("int", 9)},
    "macd_dea": {"fast": ("int", 12), "slow": ("int", 26), "signal": ("int", 9)},
    "macd_hist": {"fast": ("int", 12), "slow": ("int", 26), "signal": ("int", 9)},
}

INDICATOR_INT_BOUNDS = (2, 500)
INDICATOR_WIDTH_BOUNDS = (0.1, 10.0)


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
    "INDICATOR_INT_BOUNDS",
    "INDICATOR_SPECS",
    "INDICATOR_WIDTH_BOUNDS",
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
