"""行情数据本地缓存（CSV）：load/save + 双源（akshare 主、tushare 备）。"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import akshare
from .tushare_source import TushareSource

log = logging.getLogger(__name__)
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "cache"


def _resolve_code(code: str) -> str:
    """把 6 位代码统一成存储名。"""
    return str(code).strip().zfill(6)


def csv_path(code: str) -> Path:
    return CACHE_DIR / f"{_resolve_code(code)}.csv"


def save_csv(df: pd.DataFrame, code: str) -> Path:
    p = csv_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, p)
    return p


def load_csv(code: str) -> pd.DataFrame | None:
    p = csv_path(code)
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _try_tushare(code: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        src = TushareSource()
        return src.fetch_daily(code, start, end)
    except Exception as e:
        log.warning("tushare fallback failed for %s: %s", code, e)
        return None


def fetch_daily(code: str, start: str, end: str) -> pd.DataFrame:
    """优先 akshare；akshare 失败再试 tushare。返回前复价 OHLCV。"""
    code = _resolve_code(code)
    p = csv_path(code)
    if p.exists():
        df = load_csv(code)
        if df is not None and len(df) > 0:
            return df
    # 先读已有缓存合并（增量）
    existing = load_csv(code)
    try:
        fresh = akshare.fetch_ashare(code, start, end)
    except Exception as e:
        log.warning("akshare failed for %s (%s): %s", code, start, e)
        fallback = _try_tushare(code, start, end)
        if fallback is None:
            if existing is not None and len(existing) > 0:
                return existing
            raise
        fresh = fallback
    if existing is not None and len(existing) > 0:
        # 按日期合并，新的覆盖旧的
        merged = pd.concat([existing, fresh]).drop_duplicates(subset=["date"], keep="last")
        merged = merged.sort_values("date")
        save_csv(merged, code)
        return merged
    save_csv(fresh, code)
    return fresh


def get_code(code: str, start: str, end: str) -> pd.DataFrame:
    """便利函数。"""
    return fetch_daily(code, start, end)
