"""行情与标的：K 线、快照、排行榜、标的详情。"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "BarOut",
    "BarsResponse",
    "IndexQuoteOut",
    "InstrumentOut",
    "MarketOut",
    "QuoteOut",
    "QuoteResponse",
    "RankingOut",
    "SymbolOut",
    "WatchlistUpdate",
]


class BarOut(BaseModel):
    """单根 K 线。``limit_up`` / ``limit_down`` 为当日真实涨跌停价（已含 ST、北交所等差异）。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    prev_close: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None


class BarsResponse(BaseModel):
    symbol: str
    code: str = ""
    market: str = "CN"
    currency: str = "CNY"
    name: str = ""
    bars: list[BarOut]
    source: str = ""
    cached: bool = False


class QuoteOut(BaseModel):
    symbol: str
    name: str = ""
    price: float = 0.0
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None  # 股（三市场统一）
    amount: float | None = None  # 本币元
    change: float | None = None
    change_pct: float | None = None
    ts: str = ""
    source: str = ""
    stale: bool = False
    market: str = "CN"
    currency: str = "CNY"


class QuoteResponse(BaseModel):
    quotes: dict[str, QuoteOut]
    ts: str
    note: str = ""


class IndexQuoteOut(BaseModel):
    """指数卡片：点数 + 涨跌（不可交易，独立于 MARKET:CODE 体系）。"""

    id: str
    name: str
    market: str
    currency: str
    price: float
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None


class RankingOut(BaseModel):
    """市场排行榜：涨幅榜 / 跌幅榜 / 成交额榜（热门推荐复用成交额榜）。"""

    market: str
    sort: str = "change_pct"
    order: str = "desc"
    period: str = "day"
    items: list[QuoteOut] = Field(default_factory=list)
    note: str = ""
    ts: str = ""


class SymbolOut(BaseModel):
    symbol: str
    code: str = ""
    name: str = ""
    board: str = ""
    industry: str = ""
    is_st: bool = False
    market: str = "CN"
    kind: str = "stock"  # stock / etf / index / other


class InstrumentOut(BaseModel):
    """标的详情：标识 + 交易规则 + 最新快照（详情页一屏所需）。"""

    symbol: str
    code: str
    market: str
    currency: str
    currency_symbol: str = ""
    name: str = ""
    board: str = ""
    limit_pct: float = 0.0
    lot_size: int = 1
    t_plus_1: bool = True
    price_digits: int = 2
    price: float = 0.0
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    source: str = ""


class MarketOut(BaseModel):
    """市场元信息，前端市场分类标签用。"""

    code: str  # CN / US / HK
    label: str  # A股 / 美股 / 港股
    currency: str  # CNY / USD / HKD
    currency_symbol: str  # ¥ / $ / HK$


class WatchlistUpdate(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=30)
