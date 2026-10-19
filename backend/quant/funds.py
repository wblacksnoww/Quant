"""场外基金：申赎流水聚合为持仓与收益。

**为什么不进事件驱动引擎**：场外基金按 T 日净值确认份额、无盘中撮合、无买卖盘，
把它塞进逐 bar 撮合只会制造假精度。这里用纯函数按流水记账：

* 申购：支付金额（含费）→ ``份额 = (金额 - 申购费) / 净值``；
* 赎回：申报份额 → ``到账 = 份额 × 净值 - 赎回费``，按加权平均成本结转已实现盈亏。

持仓不落库，每次由流水聚合（见 :class:`FundTrade`），避免双写不一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Protocol

SIDE_SUBSCRIBE = "subscribe"
SIDE_REDEEM = "redeem"
SIDES = (SIDE_SUBSCRIBE, SIDE_REDEEM)

_EPS = 1e-9


class FundTradeLike(Protocol):
    """聚合所需的最小字段集（ORM 记录与测试用的简单对象都满足）。"""

    symbol: str
    side: str
    shares: float
    amount: float
    fee: float
    nav: float
    trade_date: date


@dataclass(slots=True)
class FundPosition:
    """单只基金的持仓聚合。``cost`` 为含费成本（元）。"""

    symbol: str
    name: str = ""
    shares: float = 0.0
    cost: float = 0.0
    realized_pnl: float = 0.0
    trades: int = 0

    @property
    def avg_cost(self) -> float:
        return self.cost / self.shares if self.shares > _EPS else 0.0

    @property
    def is_flat(self) -> bool:
        return self.shares <= _EPS

    def to_dict(self, nav: float | None = None) -> dict:
        """净值 ``nav`` 缺省时市值按成本计（未知行情不虚构收益）。"""
        price = nav if nav and nav > 0 else self.avg_cost
        market_value = self.shares * price
        unrealized = market_value - self.cost
        return {
            "symbol": self.symbol,
            "name": self.name,
            "shares": round(self.shares, 4),
            "avg_cost": round(self.avg_cost, 4),
            "cost": round(self.cost, 2),
            "nav": round(price, 4) if price else 0.0,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_pnl": round(unrealized + self.realized_pnl, 2),
            "return_pct": round(unrealized / self.cost, 6) if self.cost > _EPS else 0.0,
            "trades": self.trades,
        }


@dataclass(slots=True)
class PortfolioSummary:
    """全部基金持仓的合计口径。"""

    positions: dict[str, FundPosition] = field(default_factory=dict)

    @property
    def cost(self) -> float:
        return sum(p.cost for p in self.positions.values())

    @property
    def realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())

    def totals(self, navs: dict[str, float] | None = None) -> dict:
        navs = navs or {}
        rows = [p.to_dict(navs.get(p.symbol)) for p in self.positions.values()]
        market_value = sum(r["market_value"] for r in rows)
        cost = sum(r["cost"] for r in rows)
        unrealized = sum(r["unrealized_pnl"] for r in rows)
        realized = sum(r["realized_pnl"] for r in rows)
        return {
            "cost": round(cost, 2),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(realized, 2),
            "total_pnl": round(unrealized + realized, 2),
            "return_pct": round(unrealized / cost, 6) if cost > _EPS else 0.0,
            "count": len(rows),
        }


def compute_positions(
    trades: Iterable[FundTradeLike],
    *,
    names: dict[str, str] | None = None,
) -> PortfolioSummary:
    """按时间顺序聚合流水。调用方需保证 ``trades`` 已按（交易日, id）升序。

    赎回份额超过持仓的部分不记账（并保持持仓为 0），由路由层先行校验拦截；
    纯函数这里只保证不产生负持仓。
    """
    names = names or {}
    out: dict[str, FundPosition] = {}
    for trade in trades:
        pos = out.setdefault(
            trade.symbol, FundPosition(symbol=trade.symbol, name=names.get(trade.symbol, ""))
        )
        pos.trades += 1
        if trade.side == SIDE_SUBSCRIBE:
            pos.shares += trade.shares
            pos.cost += trade.amount
            continue
        if trade.side != SIDE_REDEEM:
            continue
        sellable = min(trade.shares, pos.shares)
        unit_cost = pos.avg_cost
        pos.shares = max(0.0, pos.shares - sellable)
        pos.cost = max(0.0, pos.cost - unit_cost * sellable)
        # 到账金额按实际申报份额计算（超额部分已由路由层拦截）
        pos.realized_pnl += trade.amount - unit_cost * sellable
        if pos.shares <= _EPS:
            pos.shares = 0.0
            pos.cost = 0.0
    return PortfolioSummary(positions={k: v for k, v in out.items() if not v.is_flat or v.trades})


__all__ = [
    "SIDES",
    "SIDE_REDEEM",
    "SIDE_SUBSCRIBE",
    "FundPosition",
    "FundTradeLike",
    "PortfolioSummary",
    "compute_positions",
]
