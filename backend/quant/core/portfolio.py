"""账户与持仓：现金、冻结资金、T+1 可用数量、已实现/未实现盈亏。

被回测引擎与纸盘引擎共用。所有金额单位为元，数量单位为股。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from .errors import InsufficientCash, InsufficientMargin, InsufficientPosition, ShortNotAllowed
from .markets import rules_for
from .types import AccountSnapshot, Fill, Position, Side, utcnow


class Account:
    """交易账户。

    成本价口径：**含费摊薄**。开仓时 ``avg_cost`` 会把费用摊进成本；
    平仓时用当时 ``avg_cost`` 结算已实现盈亏，并分摊费用。
    这样"已实现盈亏 + 未实现盈亏"才是投资者真正关心的净收益。

    做空口径：``quantity`` 允许为负。开空时卖出所得全额入现金、
    不设独立保证金账户，约束是「空头名义价值 × 保证金率 ≤ 权益」
    （默认保证金率 1.0，即无杠杆）。权益 = 现金 + 市值对空头自动成立：
    价格涨 → 市值为负且绝对值变大 → 权益降。**现金永不为负**这条
    底线不变——空头浮亏吃掉的是权益，买回时现金不足会被拒。
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        name: str = "default",
        *,
        short_margin_ratio: float = 1.0,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash 必须为正数")
        if short_margin_ratio <= 0:
            raise ValueError("short_margin_ratio 必须为正数")
        self.name = name
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.frozen_cash = 0.0
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.turnover = 0.0
        self.trade_count = 0
        self.short_margin_ratio = float(short_margin_ratio)
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

    def gross_exposure(self) -> float:
        """总敞口 = Σ|持仓市值|。空头市值是负数，直接求和会互相抵消，
        权益口径的仓位比例必须用绝对值。"""
        return sum(abs(p.market_value) for p in self.positions.values())

    def short_notional(self) -> float:
        """空头名义价值合计（按最新价），开空保证金校验用。"""
        return sum(abs(p.market_value) for p in self.positions.values() if p.is_short)

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
    def _split_close_open(self, symbol: str, side: Side, quantity: float) -> tuple[float, float]:
        """把一笔成交拆成「平仓量」与「开仓量」。

        方向与持仓相反的部分先平仓（多余部分穿过零点反向开仓），
        同向部分全是加仓。四个象限（开多/平多/开空/平空）都由这一
        个分解导出，结算与预估已实现盈亏共用，保证两处口径一致。
        """
        pos = self.get_position(symbol)
        delta = side.sign * quantity  # 该笔成交对持仓的符号
        if pos.quantity != 0 and (pos.quantity > 0) != (delta > 0):
            close_qty = min(abs(pos.quantity), quantity)
            return close_qty, quantity - close_qty
        return 0.0, quantity

    def realized_pnl_for(
        self, symbol: str, side: Side, price: float, quantity: float, fees_total: float
    ) -> float:
        """预估一笔成交的已实现盈亏（平仓部分非零，纯开仓为 0）。

        下单撮合前用它给 :class:`~quant.core.types.Fill` 填上 ``realized_pnl``，
        与 :meth:`apply_fill` 内部用的是同一公式，保证账面与实际一致。
        注意买入也可能产生已实现盈亏——买回平空。
        """
        close_qty, _ = self._split_close_open(symbol, side, quantity)
        if close_qty <= 0:
            return 0.0
        pos = self.get_position(symbol)
        fees_close = fees_total * (close_qty / quantity) if quantity > 0 else fees_total
        if pos.is_short:
            return (pos.avg_cost - price) * close_qty - fees_close
        return (price - pos.avg_cost) * close_qty - fees_close

    def apply_fill(self, fill: Fill) -> float:
        """结算一笔成交，返回该笔的已实现盈亏（平仓部分非零）。

        一笔成交先按方向拆成「平仓 + 开仓」两段（见 :meth:`_split_close_open`），
        费用按比例分摊：平仓段的费用进已实现盈亏，开仓段的费用摊进成本。
        现金流只有一种：买入付 ``gross + fees``，卖出收 ``gross - fees``，
        无论这笔单是平仓还是开空。

        **账户现金不允许为负**——负现金意味着策略用了不存在的杠杆。
        撮合层已按可用资金缩减买入数量，这里是最后一道闸门。
        开空另有一道保证金闸门：空头名义价值 × 保证金率不得超过权益。
        """
        pos = self.get_position(fill.symbol)
        now = fill.dt or utcnow()
        rules = rules_for(fill.symbol)
        qty = fill.quantity
        close_qty, open_qty = self._split_close_open(fill.symbol, fill.side, qty)
        fees_close = fill.fees.total * (close_qty / qty) if qty > 0 else fill.fees.total
        fees_open = fill.fees.total - fees_close

        # --- 闸门 1：市场资格。can_short=False 的市场出现开空量直接拒绝 ---- #
        if open_qty > 0 and fill.side is Side.SELL and not rules.can_short:
            raise ShortNotAllowed(
                f"{fill.symbol} 所在市场不允许做空，卖出 {qty} 中有 {open_qty} 会开空仓",
                symbol=fill.symbol,
            )

        # --- 闸门 2：开空保证金。名义价值 × 保证金率 ≤ 权益 ---------------- #
        # 开空这一瞬间权益不变（现金 +所得、市值 -所得），用结算前的权益校验即可。
        if open_qty > 0 and fill.side is Side.SELL:
            notional_after = (abs(pos.quantity) + open_qty) * fill.price
            equity = self.equity()
            if notional_after * self.short_margin_ratio > equity + 1e-6:
                raise InsufficientMargin(
                    f"{fill.symbol} 开空保证金不足：空头名义 {notional_after:.2f} × "
                    f"保证金率 {self.short_margin_ratio:g} 超过权益 {equity:.2f}",
                    required=notional_after * self.short_margin_ratio,
                    available=equity,
                )

        # --- 闸门 3：买入现金充足（加多与买回平空同一条）------------------- #
        if fill.side is Side.BUY:
            cost = fill.gross + fill.fees.total
            if cost > self.available_cash + 1e-6:
                raise InsufficientCash(
                    f"{fill.symbol} 成交需要 {cost:.2f}，可用资金 {self.available_cash:.2f}；"
                    "撮合层应已按可用资金缩减数量，此处出现说明存在跳空/费用漏检",
                    required=cost,
                    available=self.available_cash,
                )

        realized = 0.0

        # --- 平仓段：按持仓方向结算已实现盈亏 ----------------------------- #
        if close_qty > 0:
            if pos.is_short:
                realized = (pos.avg_cost - fill.price) * close_qty - fees_close
            else:
                realized = (fill.price - pos.avg_cost) * close_qty - fees_close
            pos.realized_pnl += realized
            pos.quantity += fill.side.sign * close_qty
            if pos.quantity == 0:
                pos.avg_cost = 0.0
                pos.opened_at = None

        # --- 开仓段：费用摊进成本。方向不同摊法不同： --------------- #
        # 多头成本 = 买入金额 + 费用（花出去的都算成本）；
        # 空头成本 = 卖出净所得（金额 - 费用）——空头赚的是「卖得净价 - 买回价」，
        # 把费用加进空头成本会让「已实现 + 未实现 = 总盈亏」恒等式差一笔开仓费。
        if open_qty > 0:
            new_abs = abs(pos.quantity) + open_qty
            open_gross = fill.price * open_qty
            if fill.side is Side.SELL:
                new_cost_value = abs(pos.cost_value) + open_gross - fees_open
            else:
                new_cost_value = abs(pos.cost_value) + open_gross + fees_open
            pos.avg_cost = new_cost_value / new_abs
            pos.quantity += fill.side.sign * open_qty
            pos.opened_at = pos.opened_at or now
            # T+1 市场当日买入不可平；卖出（开空）与 T+0 市场立即可平
            if not (fill.side is Side.BUY and rules.t_plus_1):
                pos.available += fill.side.sign * open_qty

        # --- 现金与统计 ---------------------------------------------------- #
        self.cash += fill.cash_flow
        self.realized_pnl += realized
        if pos.quantity == 0:
            pos.available = 0.0
        elif pos.is_short:
            # 空头只存在于 T+0 市场（can_short ⇒ t_plus_1=False），全部可平
            pos.available = pos.quantity
        else:
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
            gross_exposure=self.gross_exposure(),
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
