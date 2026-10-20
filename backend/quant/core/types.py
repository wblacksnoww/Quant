"""领域基础类型：行情、订单、成交、持仓、账户快照。

本模块只依赖标准库。回测引擎、纸盘引擎、券商适配器全部复用这里的类型，
因此"一笔订单长什么样"在整个系统里只有一个答案。
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    """带时区的当前时间（UTC）。全系统时间统一存 UTC，展示层再转本地时区。"""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _round(value: float, digits: int = 4) -> float:
    return float(f"{value:.{digits}f}")


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """买入 +1，卖出 -1。"""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def label(self) -> str:
        return "买入" if self is Side.BUY else "卖出"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    DAY = "day"  # 当日有效（A 股默认）
    GTC = "gtc"  # 撤单前有效
    IOC = "ioc"  # 立即成交否则撤销


class OrderStatus(str, Enum):
    PENDING = "pending"  # 已创建，尚未通过风控
    SUBMITTED = "submitted"  # 已通过风控，等待撮合
    PARTIAL = "partial"  # 部分成交
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"  # 当日未成交，日终失效

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)

    @property
    def is_final(self) -> bool:
        return not self.is_open


class EngineStatus(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FINISHED = "finished"
    FAILED = "failed"


# --------------------------------------------------------------------------- #
# 行情
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Bar:
    """单根 K 线。``dt`` 为 bar 起始时间（日线即当日 00:00，分钟线为分钟起点）。

    ``prev_close`` / ``limit_up`` / ``limit_down`` 用于涨跌停撮合判定，
    数据源没有提供时由 :mod:`quant.execution.matching` 兜底推算。
    """

    symbol: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    prev_close: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    freq: str = "1d"

    @property
    def date(self) -> date:
        return self.dt.date()

    @property
    def typical(self) -> float:
        """典型价 (H+L+C)/3，成交量加权近似。"""
        return (self.high + self.low + self.close) / 3.0

    @property
    def range(self) -> float:
        return self.high - self.low

    def is_limit_up(self, tol: float = 1e-4) -> bool:
        return self.limit_up is not None and self.close >= self.limit_up - tol

    def is_limit_down(self, tol: float = 1e-4) -> bool:
        return self.limit_down is not None and self.close <= self.limit_down + tol

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "dt": self.dt.isoformat(),
            "date": self.date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "prev_close": self.prev_close,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "freq": self.freq,
        }


# --------------------------------------------------------------------------- #
# 费用
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """一笔成交的费用明细（元）。"""

    commission: float = 0.0  # 佣金（含最低 5 元约束，由 CostModel 落实）
    stamp_tax: float = 0.0  # 印花税（仅卖出）
    transfer_fee: float = 0.0  # 过户费（双向）
    slippage: float = 0.0  # 滑点成本（换算成金额）

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee + self.slippage

    def to_dict(self) -> dict[str, float]:
        return {
            "commission": _round(self.commission),
            "stamp_tax": _round(self.stamp_tax),
            "transfer_fee": _round(self.transfer_fee),
            "slippage": _round(self.slippage),
            "total": _round(self.total),
        }

    @classmethod
    def zero(cls) -> FeeBreakdown:
        return cls()


# --------------------------------------------------------------------------- #
# 订单
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Order:
    """委托单。

    ``quantity`` 始终为正数，方向由 ``side`` 决定，避免国内接口正负号约定不一的问题。
    """

    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    tif: TimeInForce = TimeInForce.DAY
    id: str = field(default_factory=lambda: new_id("ord_"))
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees: FeeBreakdown = field(default_factory=FeeBreakdown)
    created_at: datetime = field(default_factory=utcnow)
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    strategy: str = ""
    tag: str = ""
    reason: str = ""  # 拒单 / 撤销 / 过期原因
    meta: dict[str, Any] = field(default_factory=dict)

    # -- 派生属性 ---------------------------------------------------------- #
    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    @property
    def is_final(self) -> bool:
        return self.status.is_final

    @property
    def is_buy(self) -> bool:
        return self.side is Side.BUY

    def notional(self, price: float | None = None) -> float:
        px = price if price is not None else (self.avg_fill_price or self.limit_price or 0.0)
        return abs(px * self.quantity)

    def filled_notional(self) -> float:
        return abs(self.avg_fill_price * self.filled_quantity)

    # -- 状态迁移 ---------------------------------------------------------- #
    def mark_submitted(self, at: datetime | None = None) -> None:
        self.status = OrderStatus.SUBMITTED
        self.submitted_at = at or utcnow()
        self.updated_at = self.submitted_at

    def apply_fill(self, quantity: float, price: float, fees: FeeBreakdown) -> None:
        """按成交回报更新均价、数量与费用（成交均价按数量加权）。"""
        total_qty = self.filled_quantity + quantity
        if total_qty <= 0:
            return
        self.avg_fill_price = (self.avg_fill_price * self.filled_quantity + price * quantity) / total_qty
        self.filled_quantity = total_qty
        prev = self.fees
        self.fees = FeeBreakdown(
            commission=prev.commission + fees.commission,
            stamp_tax=prev.stamp_tax + fees.stamp_tax,
            transfer_fee=prev.transfer_fee + fees.transfer_fee,
            slippage=prev.slippage + fees.slippage,
        )
        self.status = OrderStatus.FILLED if self.remaining == 0 else OrderStatus.PARTIAL
        self.updated_at = utcnow()

    def reject(self, reason: str) -> None:
        self.status = OrderStatus.REJECTED
        self.reason = reason
        self.updated_at = utcnow()

    def cancel(self, reason: str = "用户撤单") -> None:
        self.status = OrderStatus.CANCELLED
        self.reason = reason
        self.updated_at = utcnow()

    def expire(self, reason: str = "当日未成交") -> None:
        self.status = OrderStatus.EXPIRED
        self.reason = reason
        self.updated_at = utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "tif": self.tif.value,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": _round(self.avg_fill_price),
            "fees": self.fees.to_dict(),
            "created_at": self.created_at.isoformat(),
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "strategy": self.strategy,
            "tag": self.tag,
            "reason": self.reason,
            "meta": self.meta,
        }


# --------------------------------------------------------------------------- #
# 成交
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Fill:
    """成交通知。不可变，便于审计与事件回溯。"""

    order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    dt: datetime
    fees: FeeBreakdown = field(default_factory=FeeBreakdown)
    id: str = field(default_factory=lambda: new_id("fl_"))
    strategy: str = ""
    tag: str = ""
    realized_pnl: float = 0.0  # 平仓部分实现的盈亏（已扣费）

    @property
    def gross(self) -> float:
        """成交金额（不含费用）。"""
        return abs(self.price * self.quantity)

    @property
    def cash_flow(self) -> float:
        """对现金的净影响：买入为负、卖出为正，均已扣费。"""
        return self.side.sign * self.gross - self.fees.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": _round(self.price),
            "gross": _round(self.gross),
            "fees": self.fees.to_dict(),
            "cash_flow": _round(self.cash_flow),
            "realized_pnl": _round(self.realized_pnl),
            "dt": self.dt.isoformat(),
            "strategy": self.strategy,
            "tag": self.tag,
        }


# --------------------------------------------------------------------------- #
# 持仓
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Position:
    """持仓。``available`` 体现 T+1：T+1 市场当日买入当日不可卖（T+0 市场例外）。"""

    symbol: str
    quantity: float = 0.0
    available: float = 0.0
    avg_cost: float = 0.0  # 含费用摊薄后的成本价
    last_price: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def cost_value(self) -> float:
        return self.avg_cost * self.quantity

    @property
    def market_value(self) -> float:
        price = self.last_price or self.avg_cost
        return price * self.quantity

    @property
    def unrealized_pnl(self) -> float:
        if self.is_flat:
            return 0.0
        return self.market_value - self.cost_value

    @property
    def unrealized_pct(self) -> float:
        if self.is_flat or self.cost_value == 0:
            return 0.0
        return self.unrealized_pnl / self.cost_value

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "available": self.available,
            "avg_cost": _round(self.avg_cost),
            "last_price": _round(self.last_price),
            "cost_value": _round(self.cost_value, 2),
            "market_value": _round(self.market_value, 2),
            "unrealized_pnl": _round(self.unrealized_pnl, 2),
            "unrealized_pct": _round(self.unrealized_pct, 6),
            "realized_pnl": _round(self.realized_pnl, 2),
            "total_pnl": _round(self.total_pnl, 2),
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
        }


# --------------------------------------------------------------------------- #
# 账户快照
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """某一时刻的账户全貌，用于权益曲线、风控与前端展示。"""

    dt: datetime
    cash: float
    frozen_cash: float
    market_value: float
    total_equity: float
    initial_cash: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    position_count: int = 0

    @property
    def total_return(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return self.total_equity / self.initial_cash - 1.0

    @property
    def cash_ratio(self) -> float:
        if self.total_equity <= 0:
            return 0.0
        return self.cash / self.total_equity

    @property
    def position_ratio(self) -> float:
        return 1.0 - self.cash_ratio

    def to_dict(self) -> dict[str, Any]:
        return {
            "dt": self.dt.isoformat(),
            "cash": _round(self.cash, 2),
            "frozen_cash": _round(self.frozen_cash, 2),
            "market_value": _round(self.market_value, 2),
            "total_equity": _round(self.total_equity, 2),
            "initial_cash": _round(self.initial_cash, 2),
            "realized_pnl": _round(self.realized_pnl, 2),
            "unrealized_pnl": _round(self.unrealized_pnl, 2),
            "total_fees": _round(self.total_fees, 2),
            "total_return": _round(self.total_return, 6),
            "cash_ratio": _round(self.cash_ratio, 6),
            "position_ratio": _round(self.position_ratio, 6),
            "position_count": self.position_count,
        }


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """除零安全的除法，指标计算里到处都要用。"""
    if denominator is None or denominator == 0 or not math.isfinite(denominator):
        return default
    result = numerator / denominator
    return result if math.isfinite(result) else default


__all__ = [
    "AccountSnapshot",
    "Bar",
    "EngineStatus",
    "FeeBreakdown",
    "Fill",
    "Order",
    "OrderStatus",
    "OrderType",
    "Position",
    "Side",
    "TimeInForce",
    "new_id",
    "safe_div",
    "utcnow",
]
