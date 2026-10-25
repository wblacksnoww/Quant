"""组合优化与指数成分股的请求/响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .runs import dedupe_symbols

__all__ = [
    "IndexMemberOut",
    "IndexSnapshotIn",
    "IndexSnapshotOut",
    "IndexSummaryOut",
    "PortfolioMethodOut",
    "PortfolioOptimizeOut",
    "PortfolioOptimizeRequest",
]

PORTFOLIO_METHOD = Literal["equal_weight", "inverse_vol", "risk_parity", "min_variance", "max_sharpe"]


class PortfolioOptimizeRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=50)
    method: PORTFOLIO_METHOD = "risk_parity"
    start: date | None = None
    end: date | None = None
    lookback_days: int = Field(default=365, ge=60, le=3650, description="未指定 start 时，回看的自然日数")
    risk_free_rate: float = Field(default=0.02, ge=-0.1, le=0.5)
    max_weight: float = Field(default=1.0, gt=0, le=1, description="单标的上限；设为 0.2 即「不超过 20%」")
    min_weight: float = Field(default=0.0, ge=0, lt=1)

    @field_validator("symbols")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return dedupe_symbols(values)

    @field_validator("max_weight")
    @classmethod
    def _check_weight(cls, value: float, info) -> float:
        low = info.data.get("min_weight", 0.0)
        if value < low:
            raise ValueError("单标的上限不能小于下限")
        return value


class PortfolioOptimizeOut(BaseModel):
    method: str
    method_label: str
    weights: dict[str, float]
    expected_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    diversification: float = 0.0
    note: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    price_start: str | None = None
    price_end: str | None = None


class PortfolioMethodOut(BaseModel):
    key: str
    label: str


class IndexSnapshotIn(BaseModel):
    """导入某一天的成分股快照。"""

    symbols: list[str] = Field(min_length=1, max_length=2000)
    as_of: date
    source: str = Field(default="manual", max_length=32)


class IndexSnapshotOut(BaseModel):
    index_code: str
    added: int
    removed: int
    kept: int
    active: int
    as_of: str


class IndexMemberOut(BaseModel):
    symbol: str
    start_date: str | None = None
    end_date: str | None = None
    source: str = "manual"


class IndexSummaryOut(BaseModel):
    index_code: str
    entries: int
    active: int
    earliest: str | None = None
    sources: list[str] = Field(default_factory=list)
