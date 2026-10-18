"""账户与持仓：现金、冻结资金、T+1 可用数量、已实现/未实现盈亏。

被回测引擎与纸盘引擎共用。所有金额单位为元，数量单位为股。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

from .errors import InsufficientCash, InsufficientPosition
from .types import AccountSnapshot, FeeBreakdown, Fill, Position, Side, utcnow


class Account:
    """交易账户。

    成本价口径：**含费摊薄**。买入时 ``avg_cost`` 会把佣金/过户费摊进成本；
    卖出时用当时 ``avg_cost`` 结算已实现盈亏，并扣除卖出费用。
    这样"已实现盈亏 + 未实现盈亏"才是投资者真正关心的净收益。
    """

    def __init__(self, initial_cash: float = 100_000.0, name: str = "default") -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash 必须为正数")
        self.name = name
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.frozen_cash = 0.0
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.turnover = 0.0
        self.trade_count = 0
        self._last_prices: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @property
    def available_cash(self) -> float:
        """可动用现金 = 现金 - 冻结。"""
        return self.cash - self.frozen_cash

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def holding_symbols(self) -> list[str]:
        return [s for s, p in self.positions.items() if not p.is_flat]

    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def equity(self) -> float:
        """总权益 = 现金 + 持仓市值（不含冻结，冻结的钱仍在现金里）。"""
        return self.cash + self.market_value()

    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    def last_price(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        if pos and pos.last_price:
            return pos.last_price
        return self._last_prices.get(symbol, 0.0)

    # ------------------------------------------------------------------ #
    # 行情驱动
    # ------------------------------------------------------------------ #
    def mark_price(self, symbol: str, price: float) -> None:
        """按最新价重估某一持仓。"""
        if not price or price <= 0:
            return
        self._last_prices[symbol] = float(price)
        pos = self.positions.get(symbol)
        if pos is not None and not pos.is_flat:
            pos.last_price = float(price)

    def mark_prices(self, prices: Mapping[str, float]) -> None:
        for symbol, price in prices.items():
            self.mark_price(symbol, price)

    # ------------------------------------------------------------------ #
    # 资金冻结（用于锁定未成交买单的购买力）
    # ------------------------------------------------------------------ #
    def freeze(self, amount: float) -> None:
        if amount <= 0:
            return
        if amount > self.available_cash + 1e-6:
            raise InsufficientCash(
                f"可动用资金不足：需要 {amount:.2f}，可用 {self.available_cash:.2f}",
                required=amount,
                available=self.available_cash,
            )
        self.frozen_cash += amount

    def unfreeze(self, amount: float) -> None:
        self.frozen_cash = max(0.0, self.frozen_cash - max(0.0, amount))

    # ------------------------------------------------------------------ #
    # 成交结算
    # ------------------------------------------------------------------ #
    def realized_pnl_for(
        self, symbol: str, side: Side, price: float, quantity: int, fees_total: float
    ) -> float:
        """预估一笔成交的已实现盈亏（买入恒为 0）。

        下单撮合前用它给 :class:`~quant.core.types.Fill` 填上 ``realized_pnl``，
        与 :meth:`apply_fill` 内部用的是同一公式，保证账面与实际一致。
        """
        if side is Side.BUY:
            return 0.0
        pos = self.get_position(symbol)
        return (price - pos.avg_cost) * quantity - fees_total

    def apply_fill(self, fill: Fill) -> float:
        """结算一笔成交，返回该笔的已实现盈亏（仅卖出非零）。"""
        pos = self.get_position(fill.symbol)
        now = fill.dt or utcnow()

        if fill.side is Side.BUY:
            new_qty = pos.quantity + fill.quantity
            # 成本 = 原有成本 + 本次买入金额 + 本次费用（费用摊进成本）
            new_cost_value = pos.cost_value + fill.gross + fill.fees.total
            pos.avg_cost = new_cost_value / new_qty
            pos.quantity = new_qty
            # T+1：当日买入不计入可卖
            pos.opened_at = pos.opened_at or now
            self.cash -= fill.gross + fill.fees.total
            realized = 0.0
        else:
            if fill.quantity > pos.quantity:
                raise InsufficientPosition(
                    f"{fill.symbol} 持仓不足：卖出 {fill.quantity}，持有 {pos.quantity}"
                )
            gross_profit = (fill.price - pos.avg_cost) * fill.quantity
            realized = gross_profit - fill.fees.total
            pos.realized_pnl += realized
            pos.quantity -= fill.quantity
            self.cash += fill.gross - fill.fees.total
            self.realized_pnl += realized
            if pos.quantity == 0:
                pos.avg_cost = 0.0
                pos.opened_at = None

        pos.available = min(pos.available, pos.quantity)
        pos.updated_at = now
        self.total_fees += fill.fees.total
        self.turnover += fill.gross
        self.trade_count += 1
        self.mark_price(fill.symbol, fill.price)
        return realized

    # ------------------------------------------------------------------ #
    # 交易日切换：T+1 解冻
    # ------------------------------------------------------------------ #
    def on_new_session(self) -> None:
        """新交易日开盘前调用：昨日买入的股票今日解锁为可卖。"""
        for pos in self.positions.values():
            pos.available = pos.quantity

    # ------------------------------------------------------------------ #
    # 快照
    # ------------------------------------------------------------------ #
    def snapshot(self, dt: datetime | None = None) -> AccountSnapshot:
        return AccountSnapshot(
            dt=dt or utcnow(),
            cash=self.cash,
            frozen_cash=self.frozen_cash,
            market_value=self.market_value(),
            total_equity=self.equity(),
            initial_cash=self.initial_cash,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl(),
            total_fees=self.total_fees,
            position_count=len(self.holding_symbols()),
        )

    def holdings(self) -> Iterable[Position]:
        return (p for p in self.positions.values() if not p.is_flat)

    def reset(self) -> None:
        """清空账户（回测复用同一引擎实例时用）。"""
        self.cash = self.initial_cash
        self.frozen_cash = 0.0
        self.positions.clear()
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.turnover = 0.0
        self.trade_count = 0
        self._last_prices.clear()

    def __repr__(self) -> str:
        return (
            f"<Account cash={self.cash:.2f} frozen={self.frozen_cash:.2f} "
            f"equity={self.equity():.2f} positions={len(self.holding_symbols())}>"
        )


__all__ = ["Account"]
