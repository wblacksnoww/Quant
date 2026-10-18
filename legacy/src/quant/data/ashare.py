"""akshare 数据源：国内 A 股日线，前复价，免 token。"""
from __future__ import annotations

import time

import akshare as ak
import pandas as pd

from .base import DataFeed, normalize


class AkShareFeed(DataFeed):
    """ak.stock_zh_a_hist：日线，adjust='qfq'（前复价）。"""

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        start_ts = start.replace("-", "") if start else "1990-01-01"
        end_ts = end.replace("-", "") if end else time.strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_ts,
            end_date=end_ts,
            adjust="qfq",
        )
        rename = {
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
            "成交额": "money",
        }
        df = df.rename(columns=rename)
        return normalize(df)


def fetch_ashare(symbol: str, start: str, end: str) -> pd.DataFrame:
    feed = AkShareFeed()
    last = None
    for attempt in range(3):
        try:
            return feed.fetch_daily(symbol, start, end)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.5)
    raise RuntimeError(f"akshare 取数失败（{symbol}）: {last}")
