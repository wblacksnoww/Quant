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


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """撮合结果。``fill_price`` 为 0 表示未成交。"""

    filled: bool
    fill_price: float = 0.0
    quantity: float = 0.0
    reason: BlockReason = BlockReason.NONE
    detail: str = ""

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
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.volume_limit_pct = max(0.0, min(1.0, volume_limit_pct))
        self.allow_slippage = allow_slippage
        self.is_st_map = is_st_map or {}

    # ------------------------------------------------------------------ #
    def rules(self, symbol: str) -> BoardRules:
        return rules_for(symbol, is_st=self.is_st_map.get(symbol, False))

    def limits_for(self, bar: Bar) -> tuple[float | None, float | None]:
        """优先用数据里带的涨跌停，其次按前收推算。"""
        if bar.limit_up is not None or bar.limit_down is not None:
            return bar.limit_up, bar.limit_down
        return price_limits(bar.prev_close or 0.0, bar.symbol, board=self.rules(bar.symbol))

    # ------------------------------------------------------------------ #
    def match(self, order: Order, bar: Bar) -> MatchOutcome:
        """撮合一张订单。返回 :class:`MatchOutcome`，由 Broker 决定后续。"""
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
        quantity = order.remaining
        if self.volume_limit_pct > 0 and bar.volume > 0:
            capacity = bar.volume * self.volume_limit_pct
            if capacity <= 0:
                return MatchOutcome(
                    False, reason=BlockReason.NO_LIQUIDITY, detail=f"成交量 {bar.volume:.0f} 过小"
                )
            quantity = min(quantity, capacity)

        # 买入按整手；卖出允许零股清仓
        lot = rules.lot_size
        if lot > 1 and side is Side.BUY:
            quantity = math.floor(quantity / lot) * lot
        if quantity <= 0:
            return MatchOutcome(
                False, reason=BlockReason.NO_LIQUIDITY, detail="不足一个交易单位"
            )

        return MatchOutcome(True, fill_price=price, quantity=quantity)

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
