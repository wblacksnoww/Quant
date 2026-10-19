"""场外基金数据源：akshare 开放式基金净值。

- 列表/搜索：``fund_name_em``；
- 历史净值：``fund_open_fund_info_em``（单位净值走势）。

akshare 的列名随版本略有差异，这里按"含日期/含净值"的列来定位，不硬绑中文列名。
净值序列会映射成统一的 OHLCV schema（``OHLC = 净值``、``volume = 0``），
以便复用 K 线图表与缓存；**但场外基金不进入撮合引擎**（见 :mod:`quant.funds`）。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

import pandas as pd

from ...core.errors import DataNotFound, DataProviderUnavailable
from ...core.markets import Market, parse_instrument
from ..base import DataProvider, SymbolInfo, normalize_frame, register_provider

log = logging.getLogger(__name__)

_FUND_LIST_TTL = 6 * 3600.0
_NAV_TTL = 3600.0
_NAV_CACHE_MAX = 200  # 净值缓存条目上限（超出后按插入顺序淘汰最旧）


@register_provider("fund")
class FundProvider(DataProvider):
    """akshare 场外基金净值源（带进程内 TTL 缓存）。"""

    name = "fund"
    markets = ("FUND",)
    supports_realtime = False
    supports_search = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._list_cache: tuple[float, list[tuple[str, str, str]]] | None = None
        self._nav_cache: dict[str, tuple[float, pd.DataFrame]] = {}

    # ------------------------------------------------------------------ #
    def _akshare(self):
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 依赖安装问题
            raise DataProviderUnavailable("未安装 akshare，无法获取基金净值") from exc
        return ak

    def fund_list(self) -> list[tuple[str, str, str]]:
        """``[(代码, 简称, 类型), ...]``，带 6 小时缓存。"""
        now = time.monotonic()
        with self._lock:
            if self._list_cache and now - self._list_cache[0] < _FUND_LIST_TTL:
                return self._list_cache[1]
        ak = self._akshare()
        try:
            frame = ak.fund_name_em()
        except Exception as exc:  # noqa: BLE001
            raise DataProviderUnavailable(f"拉取基金列表失败: {exc}") from exc
        code_col = _find_column(frame, ("基金代码", "代码"))
        name_col = _find_column(frame, ("基金简称", "简称", "基金名称"))
        type_col = _find_column(frame, ("基金类型", "类型"))
        if code_col is None or name_col is None:
            raise DataProviderUnavailable("基金列表列格式无法识别")
        rows: list[tuple[str, str, str]] = []
        for _, row in frame.iterrows():
            code = str(row[code_col]).strip().zfill(6)
            if not code.isdigit():
                continue
            rows.append(
                (
                    code,
                    str(row[name_col]).strip(),
                    str(row[type_col]).strip() if type_col else "",
                )
            )
        rows.sort(key=lambda item: item[0])
        with self._lock:
            self._list_cache = (now, rows)
        return rows

    def _nav_frame(self, code: str) -> pd.DataFrame:
        """单只基金的历史单位净值，列名规整为 ``date/nav``。"""
        now = time.monotonic()
        with self._lock:
            hit = self._nav_cache.get(code)
            if hit and now - hit[0] < _NAV_TTL:
                return hit[1]
        ak = self._akshare()
        try:
            raw = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        except Exception as exc:  # noqa: BLE001
            raise DataProviderUnavailable(f"{code} 净值拉取失败: {exc}") from exc
        if raw is None or len(raw) == 0:
            raise DataNotFound(f"{code} 无净值数据")
        date_col = _find_column(raw, ("净值日期", "日期"))
        nav_col = _find_column(raw, ("单位净值", "净值"))
        if date_col is None or nav_col is None:
            raise DataProviderUnavailable(f"{code} 净值列格式无法识别: {list(raw.columns)}")
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(raw[date_col], errors="coerce"),
                "nav": pd.to_numeric(raw[nav_col], errors="coerce"),
            }
        ).dropna()
        out = out[out["nav"] > 0].sort_values("date").drop_duplicates("date", keep="last")
        if len(out) == 0:
            raise DataNotFound(f"{code} 净值清洗后为空")
        out = out.reset_index(drop=True)
        with self._lock:
            self._nav_cache[code] = (now, out)
            while len(self._nav_cache) > _NAV_CACHE_MAX:
                self._nav_cache.pop(next(iter(self._nav_cache)))
        return out

    def nav_series(self, symbol: str, start: date, end: date) -> list[dict]:
        """净值序列（前端折线图直接用）。"""
        inst = _fund_instrument(symbol)
        frame = self._nav_frame(inst.code)
        mask = (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)
        rows = frame.loc[mask]
        return [
            {"date": row.date.date().isoformat(), "nav": round(float(row.nav), 4)}
            for row in rows.itertuples(index=False)
        ]

    def nav_on(self, symbol: str, day: date) -> float | None:
        """取指定日期或之前最近一个交易日的净值。"""
        inst = _fund_instrument(symbol)
        frame = self._nav_frame(inst.code)
        hit = frame[frame["date"].dt.date <= day]
        return float(hit["nav"].iloc[-1]) if len(hit) else None

    def latest_nav(self, symbol: str) -> float | None:
        inst = _fund_instrument(symbol)
        frame = self._nav_frame(inst.code)
        return float(frame["nav"].iloc[-1]) if len(frame) else None

    # ------------------------------------------------------------------ #
    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        inst = _fund_instrument(symbol)
        frame = self._nav_frame(inst.code)
        mask = (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)
        rows = frame.loc[mask]
        if len(rows) == 0:
            raise DataNotFound(f"{inst.id} 区间 {start}~{end} 无净值数据")
        out = pd.DataFrame(
            {
                "date": rows["date"].to_numpy(),
                "open": rows["nav"].to_numpy(),
                "high": rows["nav"].to_numpy(),
                "low": rows["nav"].to_numpy(),
                "close": rows["nav"].to_numpy(),
                "volume": 0.0,
                "amount": 0.0,
            }
        )
        return normalize_frame(out, symbol=inst.id)

    def search(
        self, keyword: str = "", limit: int = 50, *, market: str | None = None
    ) -> list[SymbolInfo]:
        wanted = (market or "").strip().upper()
        if wanted and wanted not in ("FUND", "ALL", "全部"):
            return []
        kw = keyword.strip().upper()
        out: list[SymbolInfo] = []
        for code, name, kind in self.fund_list():
            if kw and kw not in code and kw not in name.upper():
                continue
            out.append(
                SymbolInfo(
                    symbol=f"FUND:{code}",
                    name=name,
                    board=kind or "场外基金",
                    market="FUND",
                    kind="other",
                )
            )
            if len(out) >= limit:
                break
        return out


def _fund_instrument(symbol: str):
    inst = parse_instrument(symbol)
    if inst.market is not Market.FUND:
        raise ValueError(f"不是基金标的: {symbol}")
    return inst


def _find_column(frame: pd.DataFrame, keywords: tuple[str, ...]) -> str | None:
    """按关键词在列名里找（akshare 列名带后缀/空格时仍可命中）。"""
    for keyword in keywords:
        for col in frame.columns:
            if keyword in str(col):
                return str(col)
    return None


__all__ = ["FundProvider"]
