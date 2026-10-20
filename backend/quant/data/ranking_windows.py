"""榜单时间窗口：并发拉日线，计算本周/本月涨跌幅。

免费数据源没有"全市场周/月涨幅"字段（新浪排行只有当日），逐只拉全市场日线
不现实。这里对**候选集**（当日成交额榜前 N）计算窗口涨幅，接口的 ``note``
会说明样本口径，避免把近似值当成全市场精确排名。

``fetch`` 由调用方注入：A 股/港/美用腾讯 K 线（快，单只 ~0.25s），
虚拟货币用 ccxt 日线。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

log = logging.getLogger(__name__)

Fetcher = Callable[[str, date, date], pd.DataFrame]


def window_change(frame: pd.DataFrame, period: str) -> float | None:
    """按日线计算窗口涨跌幅（小数）。``frame`` 需含 ``date``/``close`` 且按日期升序。"""
    if frame is None or len(frame) < 2:
        return None
    dates = pd.to_datetime(frame["date"]).dt.date.tolist()
    closes = [float(value) for value in frame["close"].tolist()]
    last_date, last_close = dates[-1], closes[-1]
    if last_close <= 0:
        return None
    if period == "week":
        anchor = last_date - timedelta(days=last_date.weekday()) - timedelta(days=1)
    elif period == "month":
        anchor = last_date.replace(day=1) - timedelta(days=1)
    else:
        return None

    base: float | None = None
    for day, close in zip(dates, closes, strict=False):
        if day <= anchor and close > 0:
            base = close
    if base is None:
        # 区间内没有更早的收盘（新股/停牌）：退化为第一根
        base = closes[0]
    if base <= 0:
        return None
    return last_close / base - 1.0


def window_changes(
    symbols: Iterable[str],
    period: str,
    fetch: Fetcher,
    *,
    days: int = 70,
    max_workers: int = 6,
) -> dict[str, float]:
    """并发计算一批标的的窗口涨跌幅；单只失败时跳过（不阻断整张榜）。"""
    codes = list(dict.fromkeys(symbols))
    if not codes:
        return {}
    end = date.today()
    start = end - timedelta(days=days)

    def one(symbol: str) -> tuple[str, float | None]:
        try:
            return symbol, window_change(fetch(symbol, start, end), period)
        except Exception as exc:  # noqa: BLE001 - 单只失败不影响其他标的
            log.debug("窗口涨跌幅 %s 失败: %s", symbol, exc)
            return symbol, None

    out: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for symbol, change in pool.map(one, codes):
            if change is not None:
                out[symbol] = change
    return out


__all__ = ["window_change", "window_changes"]
