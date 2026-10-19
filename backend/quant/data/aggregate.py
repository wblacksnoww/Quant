"""K 线聚合：日线 → 周线 / 月线。

指数与个股的周/月 K 线由日线重采样得到（免费源没有直接的周/月接口）。
周期对齐：周线以周五为结束日（``W-FRI``），月线以月末为结束日（``ME``）；
``prev_close`` 取上一周期收盘，供前端展示涨跌幅。
"""

from __future__ import annotations

import pandas as pd

_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "prev_close",
    "limit_up",
    "limit_down",
]


def resample_ohlcv(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    """把日线 DataFrame 聚合为周线/月线。``interval`` 只接受 ``1w`` / ``1M``。"""
    if interval not in ("1w", "1M"):
        return frame
    if frame is None or len(frame) == 0:
        return frame

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date").set_index("date")
    rule = "W-FRI" if interval == "1w" else "ME"
    agg = (
        data.resample(rule)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
        )
        .dropna(subset=["open", "close"])
    )
    agg = agg.reset_index()
    # 用周期内最后一个实际交易日做标签（避免出现未来的月末/周末日期）
    last_days = data.index.to_series().resample(rule).max()
    agg["date"] = last_days.reindex(pd.DatetimeIndex(agg["date"])).to_numpy()
    agg["prev_close"] = agg["close"].shift(1)
    agg["limit_up"] = None
    agg["limit_down"] = None
    return agg[_COLUMNS]


__all__ = ["resample_ohlcv"]
