"""数据源抽象、统一 schema 与注册表。

所有 Provider 都返回**同一张表**，下游（缓存、引擎、策略）只认这套列：

============ ==========================================================
列           含义
============ ==========================================================
date         交易日（``datetime64[ns]``，当天 00:00）
open/high/low/close  前复权 OHLC
volume       成交量（股）
amount       成交额（元）
prev_close   前一日收盘（用于算涨跌停）
limit_up     涨停价（可选，数据源没给则由 :mod:`quant.execution.rules` 推算）
limit_down   跌停价
============ ==========================================================

新增数据源只需继承 :class:`DataProvider` 并加 ``@register_provider("xxx")``，
无需改动任何调用方。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable

import pandas as pd

from ..core.errors import DataNotFound
from ..core.types import Bar

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
EXTRA_COLUMNS = ["prev_close", "limit_up", "limit_down"]
ALL_COLUMNS = OHLCV_COLUMNS + EXTRA_COLUMNS

# 常见中文列名 → 标准列名
COLUMN_ALIASES: dict[str, str] = {
    "日期": "date",
    "时间": "date",
    "trade_date": "date",
    "datetime": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "成交金额": "amount",
    "money": "amount",
    "成交额(元)": "amount",
    "昨收": "prev_close",
    "前收盘": "prev_close",
    "涨停价": "limit_up",
    "跌停价": "limit_down",
}


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """标的检索结果。``symbol`` 是规范 id（如 ``CN:600519`` / ``US:AAPL``）。"""

    symbol: str
    name: str = ""
    board: str = ""
    industry: str = ""
    is_st: bool = False
    market: str = "CN"
    kind: str = "stock"  # stock / etf / index / other

    @property
    def code(self) -> str:
        """市场内代码（如 ``600519`` / ``AAPL``），解析失败时退化为原值。"""
        from ..core.markets import try_parse

        inst = try_parse(self.symbol)
        return inst.code if inst else self.symbol

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "code": self.code,
            "name": self.name,
            "board": self.board,
            "industry": self.industry,
            "is_st": self.is_st,
            "market": self.market,
            "kind": self.kind,
        }


# --------------------------------------------------------------------------- #
# 统一 schema
# --------------------------------------------------------------------------- #


def normalize_frame(df: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    """把任意数据源的 DataFrame 规整成标准 schema。"""
    if df is None or len(df) == 0:
        raise DataNotFound(f"{symbol or '数据'} 无行情数据")

    out = df.copy()
    out = out.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in out.columns})
    out.columns = [str(c).strip().lower() for c in out.columns]

    if "date" not in out.columns:
        # 有时日期在 index 里
        if isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index().rename(columns={out.index.name or "index": "date"})
        else:
            raise DataNotFound(f"{symbol or '数据'} 缺少日期列，实际列: {list(out.columns)}")

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    out["volume"] = out["volume"].abs()
    out["amount"] = out["amount"].abs()
    out = out[out["close"] > 0]

    if len(out) == 0:
        raise DataNotFound(f"{symbol or '数据'} 清洗后为空")

    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    out = out.reset_index(drop=True)

    # 推导 prev_close / 涨跌停
    if "prev_close" not in out.columns or out["prev_close"].isna().all():
        out["prev_close"] = out["close"].shift(1)
    else:
        out["prev_close"] = pd.to_numeric(out["prev_close"], errors="coerce")
        out["prev_close"] = out["prev_close"].fillna(out["close"].shift(1))

    if "limit_up" not in out.columns:
        out["limit_up"] = None
    if "limit_down" not in out.columns:
        out["limit_down"] = None

    if symbol and "symbol" in out.columns:
        out = out.drop(columns=["symbol"])

    return out[[*OHLCV_COLUMNS, *EXTRA_COLUMNS]]


def frame_to_bars(df: pd.DataFrame, symbol: str, freq: str = "1d") -> list[Bar]:
    """DataFrame → ``list[Bar]``，供引擎逐根推送。"""
    bars: list[Bar] = []
    for row in df.itertuples(index=False):
        bars.append(
            Bar(
                symbol=symbol,
                dt=pd.Timestamp(row.date).to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                amount=float(row.amount),
                prev_close=_maybe_float(getattr(row, "prev_close", None)),
                limit_up=_maybe_float(getattr(row, "limit_up", None)),
                limit_down=_maybe_float(getattr(row, "limit_down", None)),
                freq=freq,
            )
        )
    return bars


def bars_to_frame(bars: Iterable[Bar]) -> pd.DataFrame:
    rows = [
        {
            "date": pd.Timestamp(b.dt),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "amount": b.amount,
            "prev_close": b.prev_close,
            "limit_up": b.limit_up,
            "limit_down": b.limit_down,
        }
        for b in bars
    ]
    return pd.DataFrame(rows, columns=ALL_COLUMNS)


def _maybe_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_date(value: str | date | datetime | None, default: date | None = None) -> date:
    if value is None:
        if default is None:
            raise ValueError("日期不能为空")
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


# --------------------------------------------------------------------------- #
# Provider 抽象与注册表
# --------------------------------------------------------------------------- #


class DataProvider(ABC):
    """行情数据源接口。实现 :meth:`daily` 即可接入系统。

    ``markets`` 声明本数据源覆盖的市场（空元组 = 通用/未声明）。
    数据服务按市场把请求路由到匹配的 Provider，避免"用股票数据源去拉
    虚拟货币"这类跨市场误调用（``CRYPTO:`` 标的会被 akshare 当成港股）。
    """

    name: str = "base"
    markets: tuple[str, ...] = ()
    supports_realtime: bool = False
    supports_search: bool = False

    @abstractmethod
    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        """返回标准 schema 的日线表。"""

    def realtime(self, symbols: Iterable[str]) -> dict[str, float]:
        """实时快照（仅最新价）。不支持则抛 NotImplementedError。"""
        raise NotImplementedError(f"{self.name} 不支持实时行情")

    def snapshot(self, symbols: Iterable[str]) -> dict[str, "RealtimeQuote"]:
        """富快照（名称/涨跌/量额等）。默认用 ``realtime()`` 的现价拼一个最小结构；
        真正支持实时行情的 Provider（如 akshare 多源聚合）会覆盖它。"""
        from .realtime import RealtimeQuote  # 惰性导入避免循环依赖

        return {
            code: RealtimeQuote(symbol=code, price=price, source=self.name)
            for code, price in self.realtime(symbols).items()
        }

    def search(
        self, keyword: str = "", limit: int = 50, *, market: str | None = None
    ) -> list[SymbolInfo]:
        """标的检索；``market`` 限定市场（``CN``/``US``/``HK``/``None`` 全部）。"""
        raise NotImplementedError(f"{self.name} 不支持标的检索")

    def ranking(
        self,
        market: str,
        *,
        sort: str = "change_pct",
        order: str = "desc",
        limit: int = 20,
    ) -> list["RealtimeQuote"]:
        """市场排行榜（涨幅/成交额）。不支持的数据源抛 ``NotImplementedError``。"""
        raise NotImplementedError(f"{self.name} 不支持排行榜")

    def calendar(self, start: date, end: date) -> list[date]:
        """交易日历；默认退化为工作日。"""
        from .calendar import weekday_range

        return weekday_range(start, end)

    def intraday(self, symbol: str, *, freq: str = "5m", limit: int = 240) -> pd.DataFrame:
        """分钟 K 线（今日/本周视图）。不支持的数据源抛 ``NotImplementedError``。"""
        raise NotImplementedError(f"{self.name} 不支持分钟 K 线")

    def __repr__(self) -> str:
        return f"<DataProvider {self.name}>"


_PROVIDERS: dict[str, Callable[[], type[DataProvider]]] = {}


def register_provider(name: str) -> Callable[[type[DataProvider]], type[DataProvider]]:
    """类装饰器：把 Provider 注册进全局表。"""

    def deco(cls: type[DataProvider]) -> type[DataProvider]:
        cls.name = name
        _PROVIDERS[name] = lambda: cls
        return cls

    return deco


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def create_provider(name: str, **kwargs: Any) -> DataProvider:
    """按名字实例化 Provider（配置驱动，配置变了不用改代码）。"""
    if name not in _PROVIDERS:
        raise ValueError(f"未知数据源 {name!r}，可用：{available_providers()}")
    factory = _PROVIDERS[name]
    cls = factory()
    return cls(**kwargs) if kwargs else cls()


__all__ = [
    "ALL_COLUMNS",
    "DataProvider",
    "EXTRA_COLUMNS",
    "OHLCV_COLUMNS",
    "SymbolInfo",
    "available_providers",
    "bars_to_frame",
    "create_provider",
    "frame_to_bars",
    "normalize_frame",
    "register_provider",
    "to_date",
]
