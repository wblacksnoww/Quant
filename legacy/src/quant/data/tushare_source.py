"""tushare 备用源（需 .env 中 TUSHARE_TOKEN）。"""
from __future__ import annotations

import os

import pandas as pd

COLS = ["date", "open", "high", "low", "close", "volume", "money"]


class TushareSource:
    def __init__(self):
        self.token = os.getenv("TUSHARE_TOKEN", "")
        if not self.token:
            self._handle = None

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        if not self.token:
            raise RuntimeError("TUSHARE_TOKEN 未设置，tushare 源不可用")
        import tushare as ts
        pro = ts.pro_api(self.token)
        df = pro.daily(ts_code=f"SH.{symbol}".replace("SH.", "SH:") if False else f"SH.{symbol}",
                       start_date=start.replace("-", ""),
                       end_date=end.replace("-", ""))
        df["date"] = df["trade_date"]
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low", "close": "close",
            "volume": "volume", "amount": "money",
        })
        out = df[COLS].reset_index(drop=True)
        return out
