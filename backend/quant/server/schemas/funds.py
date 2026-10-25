"""场外基金：净值、申赎记录、持仓与组合汇总。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "FundHoldingOut",
    "FundNavOut",
    "FundNavPoint",
    "FundPortfolioOut",
    "FundTradeCreate",
    "FundTradeOut",
]


class FundNavPoint(BaseModel):
    date: str
    nav: float


class FundNavOut(BaseModel):
    symbol: str
    name: str = ""
    points: list[FundNavPoint] = Field(default_factory=list)


class FundTradeCreate(BaseModel):
    """申购填 ``amount``（支付金额），赎回填 ``shares``（申报份额）。"""

    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["subscribe", "redeem"]
    trade_date: str | None = None  # YYYY-MM-DD，缺省今天
    confirm_date: str | None = None
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    nav: float | None = Field(default=None, gt=0, description="缺省取该日或之前最近净值")
    fee_rate: float = Field(default=0.0, ge=0.0, le=0.5, description="申/赎费率，如 0.0015")
    note: str = Field(default="", max_length=255)


class FundTradeOut(BaseModel):
    id: int
    symbol: str
    name: str = ""
    side: str
    trade_date: str | None = None
    confirm_date: str | None = None
    nav: float
    shares: float
    amount: float
    fee_rate: float = 0.0
    fee: float = 0.0
    note: str = ""
    created_at: str | None = None


class FundHoldingOut(BaseModel):
    symbol: str
    name: str = ""
    shares: float
    avg_cost: float
    cost: float
    nav: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    return_pct: float
    trades: int


class FundPortfolioOut(BaseModel):
    holdings: list[FundHoldingOut] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    nav_note: str = ""
