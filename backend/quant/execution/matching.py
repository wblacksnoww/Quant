"""撮合引擎：把「订单 + 一根 bar」映射成「成交」。

**没有未来函数**是这里的第一原则：

* 市价单按该 bar 的**开盘价**成交（订单是在上一根 bar 收盘后下达的）。
* 限价单需要价格穿越：买单要求 ``bar.low <= 限价``，卖单要求 ``bar.high >= 限价``，
  成交价取「限价」与「开盘价」中对下单方更保守的那个。
* 一字板（``bar.low >= 涨停`` 或 ``bar.high <= 跌停``）无法成交，理由会写进
  ``RejectReason``，方便前端解释"为什么策略想买却买不进"。
* 成交量上限约束：单根 bar 成交不超过该 bar 成交量的 ``volume_limit_pct``，
  避免策略在小盘股上凭空吃掉整个市场。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ..core.types import Bar, FeeBreakdown, Order, OrderType, Side
from .cost import CostModel
from .rules import BoardRules, price_limits, rules_for


class BlockReason(str, Enum):
    NONE = "none"
    SUSPENDED = "suspended"  # 停牌（无成交量）
    LIMIT_UP = "limit_up"  # 一字涨停买不进
    LIMIT_DOWN = "limit_down"  # 一字跌停卖不出
    PRICE_NOT_TOUCHED = "price_not_touched"  # 限价未触及
    NO_LIQUIDITY = "no_liquidity"  # 量能不足
    NO_PRICE = "no_price"
    INSUFFICIENT_CASH = "insufficient_cash"  # 跳空/费用导致可用资金不足，已缩减数量


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """撮合结果。``fill_price`` 为 0 表示未成交。"""

    filled: bool
    fill_price: float = 0.0
    quantity: float = 0.0
    reason: BlockReason = BlockReason.NONE
    detail: str = ""
    # 因可用资金不足而缩减后的成交数量（原始撮合数量见 ``requested``）。
    # 为 True 时 ``reason`` 仍是 NONE（确实成交了），但调用方应记录该降级原因。
    cash_capped: bool = False
    requested: float = 0.0

    @property
    def blocked(self) -> bool:
        return not self.filled


class MatchingEngine:
    """无状态撮合器（除配置外不持有状态），便于在多引擎间共享与单测。"""

    def __init__(
        self,
        cost_model: CostModel | None = None,
        *,
        volume_limit_pct: float = 0.10,
        allow_slippage: bool = True,
        is_st_map: dict[str, bool] | None = None,
        impact_coef: float = 0.0,
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.volume_limit_pct = max(0.0, min(1.0, volume_limit_pct))
        self.allow_slippage = allow_slippage
        self.is_st_map = is_st_map or {}
        # 量价冲击系数：price *= (1 + impact_coef * sqrt(成交占比))，0 表示不建模
        self.impact_coef = max(0.0, float(impact_coef))

    # ------------------------------------------------------------------ #
    def rules(self, symbol: str) -> BoardRules:
        return rules_for(symbol, is_st=self.is_st_map.get(symbol, False))

    def limits_for(self, bar: Bar) -> tuple[float | None, float | None]:
        """优先用数据里带的涨跌停，其次按前收推算。

        数据源在「疑似除权日」会把涨跌停置空（推算值不可信），此时若回落到
        ``price_limits(prev_close)`` 又等于把坏推算捡回来，所以这里显式区分：
        数据带字段就**只信字段**，字段为空表示"本日无可信涨跌停"，返回 (None, None)。
        """
        if bar.limit_up is not None or bar.limit_down is not None:
            return bar.limit_up, bar.limit_down
        if not bar.prev_close or bar.prev_close <= 0:
            return None, None
        return price_limits(bar.prev_close, bar.symbol, board=self.rules(bar.symbol))

    # ------------------------------------------------------------------ #
    def match(
        self,
        order: Order,
        bar: Bar,
        *,
        available_cash: float | None = None,
    ) -> MatchOutcome:
        """撮合一张订单。返回 :class:`MatchOutcome`，由 Broker 决定后续。

        ``available_cash`` 传入时（买入单必传），成交数量会被可用现金上限约束。
        这是**跳空保护**：下单时按昨收冻结，但市价单按次日开盘价成交，
        跳空高开会让原数量买不起。真实券商会拒单或部分成交，绝不会让账户变负，
        所以这里按「能买得起多少就成交多少」缩减，并打上 ``cash_capped`` 标记。
        """
        symbol = bar.symbol
        rules = self.rules(symbol)

        if bar.volume is not None and bar.volume <= 0:
            return MatchOutcome(False, reason=BlockReason.SUSPENDED, detail="当日无成交（停牌）")
        if not bar.open or bar.open <= 0:
            return MatchOutcome(False, reason=BlockReason.NO_PRICE, detail="缺少开盘价")

        limit_up, limit_down = self.limits_for(bar)
        side = order.side

        # --- 一字板判定 ------------------------------------------------ #
        if rules.limit_pct > 0:
            if side is Side.BUY and limit_up and bar.low >= limit_up - 1e-6:
                return MatchOutcome(
                    False, reason=BlockReason.LIMIT_UP, detail=f"一字涨停（{limit_up:.2f}）买不进"
                )
            if side is Side.SELL and limit_down and bar.high <= limit_down + 1e-6:
                return MatchOutcome(
                    False,
                    reason=BlockReason.LIMIT_DOWN,
                    detail=f"一字跌停（{limit_down:.2f}）卖不出",
                )

        # --- 确定基准成交价 -------------------------------------------- #
        if order.order_type is OrderType.MARKET:
            base_price = bar.open
        else:
            limit_price = float(order.limit_price or 0.0)
            if limit_price <= 0:
                return MatchOutcome(False, reason=BlockReason.NO_PRICE, detail="限价单缺少价格")
            if side is Side.BUY:
                if bar.low > limit_price + 1e-9:
                    return MatchOutcome(
                        False,
                        reason=BlockReason.PRICE_NOT_TOUCHED,
                        detail=f"最低价 {bar.low:.2f} 未触及限价 {limit_price:.2f}",
                    )
                base_price = min(limit_price, bar.open)
            else:
                if bar.high < limit_price - 1e-9:
                    return MatchOutcome(
                        False,
                        reason=BlockReason.PRICE_NOT_TOUCHED,
                        detail=f"最高价 {bar.high:.2f} 未触及限价 {limit_price:.2f}",
                    )
                base_price = max(limit_price, bar.open)

        # --- 滑点 ------------------------------------------------------- #
        price = self.cost_model.slippage_price(side, base_price) if self.allow_slippage else base_price

        # --- 成交量约束 -------------------------------------------------- #
        requested = order.remaining
        quantity = requested
        if self.volume_limit_pct > 0 and bar.volume > 0:
            capacity = bar.volume * self.volume_limit_pct
            if capacity <= 0:
                return MatchOutcome(
                    False, reason=BlockReason.NO_LIQUIDITY, detail=f"成交量 {bar.volume:.0f} 过小"
                )
            quantity = min(quantity, capacity)

        # --- 量价冲击（可选）--------------------------------------------- #
        # 吃掉 bar 的 volume_limit_pct 却按开盘价成交在物理上不自洽，
        # impact_coef > 0 时按 sqrt(参与率) 抬高/压低成交价。默认 0 保持历史行为。
        if self.impact_coef > 0 and bar.volume > 0 and quantity > 0:
            participation = min(1.0, quantity / bar.volume)
            shock = self.impact_coef * math.sqrt(participation)
            price = price * (1.0 + shock) if side is Side.BUY else price * (1.0 - shock)

        # 买入按整手；卖出允许零股清仓
        lot = rules.lot_size
        if lot > 1 and side is Side.BUY:
            quantity = math.floor(quantity / lot) * lot

        # --- 可用现金约束（跳空保护）------------------------------------- #
        cash_capped = False
        if side is Side.BUY and available_cash is not None:
            affordable = self.affordable_quantity(available_cash, price, lot)
            if affordable < quantity:
                cash_capped = True
                quantity = affordable
        if quantity <= 0:
            # 有量能但一分钱也买不起：明确告知原因，别伪装成"不足一手"
            if cash_capped or (side is Side.BUY and available_cash is not None and requested > 0):
                return MatchOutcome(
                    False,
                    reason=BlockReason.INSUFFICIENT_CASH,
                    detail=f"可用资金 {available_cash or 0:.2f} 不足以成交一手，"
                    f"成交价 {price:.2f} 高于下单时估价（跳空）",
                    requested=requested,
                )
            return MatchOutcome(False, reason=BlockReason.NO_LIQUIDITY, detail="不足一个交易单位")

        return MatchOutcome(
            True, fill_price=price, quantity=quantity, cash_capped=cash_capped, requested=requested
        )

    def affordable_quantity(self, cash: float, price: float, lot: float) -> float:
        """在 ``cash`` 内、按 ``price`` 成交价能买的最大数量（含手续费与整手约束）。

        整手市场向下取整；小数市场二分逼近。返回值保证 ``买入成本 <= cash``，
        因此成交后账户现金不会变负。
        """
        if cash <= 0 or price <= 0:
            return 0.0
        if lot > 1:
            qty = math.floor(cash / price / lot) * lot
            # 价格含费用后可能刚好超一点，逐手下调（通常 0~2 次）
            while qty > 0 and self.cost_model.estimate_buy_cost(price, qty) > cash:
                qty -= lot
            return float(max(0.0, qty))
        lo, hi = 0.0, cash / price
        for _ in range(64):
            mid = (lo + hi) / 2.0
            if self.cost_model.estimate_buy_cost(price, mid) <= cash:
                lo = mid
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------------ #
    def fees(
        self, order: Order, price: float, quantity: float, base_price: float | None = None
    ) -> FeeBreakdown:
        """计算成交费用。滑点成本单独记一笔，方便成本归因。"""
        gross = price * quantity
        base = self.cost_model.fees(order.side, price, quantity, gross=gross)
        slip = 0.0
        if self.allow_slippage and base_price is not None and base_price > 0:
            slip = abs(price - base_price) * quantity
        return FeeBreakdown(
            commission=base.commission,
            stamp_tax=base.stamp_tax,
            transfer_fee=base.transfer_fee,
            slippage=slip,
        )


__all__ = ["BlockReason", "MatchOutcome", "MatchingEngine"]
