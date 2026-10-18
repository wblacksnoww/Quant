"""akshare 数据源：国内 A 股日线（前复价 qfq），免 token。"""
from __future__ import annotations

import akshare as ak
import pandas as pd

COLS = ["date", "open", "high", "low", "close", "volume", "money"]


def _md(s: str) -> str:
    s = str(s).replace("-", "")
    return s[:4] + s[4:6] + s[6:8] if len(s) >= 8 else s


def _dashed(s: str) -> str:
    s = str(s).replace("-", "")
    return s[:4] + "-" + s[4:6] + "-" + s[6:8]


def fetch_ashare(symbol: str, start: str, end: str) -> pd.DataFrame:
    """symbol:6位代码; start/end: YYYY-MM-DD。返回 date 升序 [date,open,high,low,close,volume,money]。"""
    symbol = str(symbol).strip().zfill(6)
    start_d = _dashed(start)
    end_d = _dashed(end)
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=_md(start), end_date=_md(end), adjust="qfq",
    )
    if df is None or df.empty:
        raise RuntimeError(f"akshare 无 {symbol} 数据")
    colmap = {
        "日期": "date", "股票代码": "code",
        "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "money",
    }
    df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
    if "date" not in df.columns:
        raise RuntimeError(f"akshare 缺少日期列 {list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "close", "volume", "money"]:
        if c not in df.columns:
            df[c] = pd.to_numeric(pd.Series(None, index=df.index), errors="coerce")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["date"].between(start_d, end_d)].copy()
    df = df[["date", "open", "high", "low", "close", "volume", "money"]].reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values("date").reset_index(drop=True)
