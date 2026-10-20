"""实时 bar 合成：纸盘与实盘共用的"今日活 bar"维护。

实时行情只有最新价，没有完整的 OHLC。两个引擎的做法一致：新交易日追加一根
以首见价为 open 的活 bar，之后每轮只更新 close/high/low。volume 恒为 0，
因此实时模式必须关闭量能约束（否则所有单都会被判"无流动性"）。
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def append_today_row(frame: pd.DataFrame, today: date, price: float | None) -> pd.DataFrame:
    """追加/复用今天的活 bar（不会重复追加）。"""
    price = price or float(frame["close"].iloc[-1])
    if len(frame) and frame["date"].iloc[-1].date() == today:
        return frame
    row = {
        "date": pd.Timestamp(today),
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 0.0,
        "amount": 0.0,
        "prev_close": float(frame["close"].iloc[-1]),
        "limit_up": None,
        "limit_down": None,
    }
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


def update_today_row(frame: pd.DataFrame, price: float) -> pd.DataFrame:
    """用最新价更新最后一根 bar 的 close/high/low。"""
    if len(frame) == 0:
        return frame
    idx = frame.index[-1]
    frame = frame.copy()
    frame.loc[idx, "close"] = price
    frame.loc[idx, "high"] = max(float(frame.loc[idx, "high"]), price)
    frame.loc[idx, "low"] = min(float(frame.loc[idx, "low"]), price)
    frame.loc[idx, "amount"] = price * float(frame.loc[idx, "volume"])
    return frame


__all__ = ["append_today_row", "update_today_row"]
