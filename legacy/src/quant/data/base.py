"""行情数据基类与缓存路径工具。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

OHLCV_COLS = ["date", "open", "high", "low", "close", "volume", "money"]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一为前复价 OHLCV，date 为 'YYYY-MM-DD' 字符串，按 date 升序。"""
    df = df.copy()
    df = df.reindex(columns=OHLCV_COLS)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for c in OHLCV_COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["money"] = pd.to_numeric(df["money"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["date", "close"]).drop_duplicates(subset=["date"])
    return df.sort_values("date").reset_index(drop=True)


class DataFeed(ABC):
    """行情源抽象。fetch_daily 返回前复价 OHLCV DataFrame。"""

    @abstractmethod
    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        pass
