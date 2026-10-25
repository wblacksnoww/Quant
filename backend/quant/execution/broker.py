"""Broker 抽象与内存撮合实现。

**同一个** :class:`SimBroker` 同时服务回测与纸盘：

============================  ==========================  ==========================
                            回测                         纸盘
============================  ==========================  ==========================
行情来源                    历史 bar 序列                 实时行情（轮询/WS）
``match_on_submit``         ``False``（避免未来函数）      ``True``（贴近真实撮合）
时间推进                    :class:`SimClock`              :class:`RealClock`
============================  ==========================  ==========================

资金与持仓的记账只有一份实现（:class:`~quant.core.portfolio.Account`），
所以"回测赚钱、纸盘亏钱"不会来自记账口径不一致。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime

from ..core.events import EventBus
from ..core.portfolio import Account
from ..core.types import Bar, Fill, Order, OrderType, Side, TimeInForce, utcnow
from .matching import BlockReason, MatchingEngine
from .rules import is_valid_lot, round_down_lot, rules_for

log = logging.getLogger(__name__)


class Broker(ABC):
    """券商/撮合器统一接口。真实券商适配器实现同样的方法即可无缝替换。"""

    def __init__(self, account: Account, bus: EventBus | None = None, *, name: str = "broker") -> None:
        self.account = account
        self.bus = bus or EventBus(name)
        self.name = name
        self._orders: dict[str, Order] = {}

    # -- 生命周期 ---------------------------------------------------------- #
    @abstractmethod
    def submit(self, order: Order) -> Order:
        """提交订单，返回（可能已被拒绝的）订单对象。"""

    @abstractmethod
    def cancel(self, order_id: str) -> bool:
        """撤销订单，成功返回 True。"""

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Fill]:
        """推进一根 bar 并撮合，返回本根 bar 产生的成交。"""

    # -- 查询 -------------------------------------------------------------- #
    @property
    def open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.is_open]

    @property
    def all_orders(self) -> list[Order]:
        return list(self._orders.values())

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def record(self, order: Order) -> None:
        """把订单登记进订单簿（风控拒单也要留痕，便于前端解释原因）。"""
        self._orders[order.id] = order

    def cancel_all(self, reason: str = "批量撤单") -> int:
        count = 0
        for order in self.open_orders:
            if self.cancel(order.id):
                count += 1
        return count

    # -- 事件 -------------------------------------------------------------- #
    def _emit_order(self, order: Order) -> None:
        self.bus.emit_order(order, source=self.name)

    def _emit_fill(self, fill: Fill) -> None:
        self.bus.emit_fill(fill, source=self.name)


class SimBroker(Broker):
    """内存撮合券商。

    订单生命周期::

        submit() → PENDING ─(校验通过)→ SUBMITTED ─(下一根 bar)→ FILLED / PARTIAL
                 └─(校验失败)→ REJECTED                    └─(未成交)→ 保持 / EXPIRED
    """

    def __init__(
        self,
        account: Account,
        bus: EventBus | None = None,
        *,
        matching: MatchingEngine | None = None,
        match_on_submit: bool = False,
        name: str = "sim",
        max_order_age_days: int = 30,
    ) -> None:
        super().__init__(account, bus, name=name)
        self.matching = matching or MatchingEngine()
        self.cost_model = self.matching.cost_model
        self.match_on_submit = match_on_submit
        # GTC 挂单寿命（自然日）。真实券商的 GTC 也有期限，无限挂单会永久冻结资金，
        # 策略只会看到"莫名资金不足"。0 表示不限期（不推荐，仅兼容旧行为）。
        self.max_order_age_days = max(0, int(max_order_age_days))
        self._queued: list[Order] = []  # 本根 bar 期间下的单，下一根 bar 才能撮合
        self._live: list[Order] = []  # 已进入撮合队列的挂单
        self._last_bars: dict[str, Bar] = {}
        self._fills: list[Fill] = []
        self._expired_by_age = 0

    # ------------------------------------------------------------------ #
    # 提交与撤销
    # ------------------------------------------------------------------ #
    def submit(self, order: Order) -> Order:
        self._orders[order.id] = order
        order.strategy = order.strategy or ""

        reason = self._validate(order)
        if reason:
            order.reject(reason)
            self._emit_order(order)
            log.info("订单被拒 %s %s %s: %s", order.symbol, order.side.value, order.quantity, reason)
            return order

        if order.side is Side.BUY:
            frozen = self._estimate_cash(order)
            try:
                self.account.freeze(frozen)
            except Exception as exc:  # noqa: BLE001 - 资金不足直接拒单
                order.reject(str(exc))
                self._emit_order(order)
                return order
            order.meta["frozen_cash"] = frozen
        else:
            order.meta["frozen_qty"] = order.quantity

        order.mark_submitted()
        self._queued.append(order)
        self._emit_order(order)

        # 纸盘模式：市价单立即以最新价成交（贴近真实券商行为）
        if self.match_on_submit and order.order_type is OrderType.MARKET:
            bar = self._last_bars.get(order.symbol)
            if bar is not None:
                self._requeue_for_immediate(order, bar)
        return order

    def _requeue_for_immediate(self, order: Order, bar: Bar) -> None:
        """把刚下的单提到队首，用最新 bar 撮合一次。"""
        if order in self._queued:
            self._queued.remove(order)
        self._live.append(order)
        self._try_match(order, bar)

    def cancel(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_open:
            return False
        order.cancel()
        self._release_reservation(order)
        self._drop(order)
        self._emit_order(order)
        return True

    # ------------------------------------------------------------------ #
    # 行情推进
    # ------------------------------------------------------------------ #
    def on_bar(self, bar: Bar) -> list[Fill]:
        self._last_bars[bar.symbol] = bar
        self.account.mark_price(bar.symbol, bar.close)

        # 上一根 bar 期间下的单，现在才有资格撮合
        if self._queued:
            self._live.extend(self._queued)
            self._queued.clear()

        fills: list[Fill] = []
        for order in list(self._live):
            if order.symbol != bar.symbol:
                continue
            fill = self._try_match(order, bar)
            if fill is not None:
                fills.append(fill)
            if order.status.is_final:
                self._drop(order)
        return fills

    # ------------------------------------------------------------------ #
    # 撮合
    # ------------------------------------------------------------------ #
    def _try_match(self, order: Order, bar: Bar) -> Fill | None:
        if not order.is_open or order.symbol != bar.symbol:
            return None

        # 买入单必须把「本单已冻结的资金 + 剩余可用现金」一起告诉撮合器：
        # 冻结额按昨收估算，成交价按今开，跳空时原数量买不起 → 撮合器会缩减数量。
        cash_for_fill = None
        if order.side is Side.BUY:
            cash_for_fill = self.account.available_cash + float(order.meta.get("frozen_cash", 0.0) or 0.0)
        outcome = self.matching.match(order, bar, available_cash=cash_for_fill)

        if not outcome.filled:
            if outcome.reason in (
                BlockReason.SUSPENDED,
                BlockReason.LIMIT_UP,
                BlockReason.LIMIT_DOWN,
                BlockReason.INSUFFICIENT_CASH,
            ):
                order.meta["last_block"] = outcome.reason.value
                order.meta["last_block_detail"] = outcome.detail
            # 资金不足属于「这一单不可能成交」，继续挂着只会白占额度，立即终止。
            # 停牌/一字板则保留挂单（明天可能就能成交），交由日终或后续 bar 处理。
            if outcome.reason is BlockReason.INSUFFICIENT_CASH:
                order.expire(outcome.detail)
                self._release_reservation(order)
                self._drop(order)
                self._emit_order(order)
            return None

        if outcome.cash_capped:
            # 跳空导致买不起原数量：记录下来，前端可解释"为什么只成交了部分"
            order.meta["cash_capped"] = True
            order.meta["cash_capped_detail"] = (
                f"跳空保护：原计划 {outcome.requested:.0f} 股，按可用资金缩减为 {outcome.quantity:.0f} 股"
            )
            log.warning(
                "跳空缩减成交 %s %s: 计划 %.0f → 实际 %.0f（成交价 %.2f）",
                order.symbol,
                order.id,
                outcome.requested,
                outcome.quantity,
                outcome.fill_price,
            )

        quantity = outcome.quantity
        if order.side is Side.SELL and quantity > self.account.get_position(order.symbol).quantity:
            quantity = self.account.get_position(order.symbol).quantity
        if quantity <= 0:
            return None

        price = outcome.fill_price
        base_price = bar.open
        fees = self.matching.fees(order, price, quantity, base_price)
        realized = self.account.realized_pnl_for(order.symbol, order.side, price, quantity, fees.total)
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            dt=bar.dt,
            fees=fees,
            strategy=order.strategy,
            tag=order.tag,
            realized_pnl=realized,
        )

        # 释放提交时冻结的资金/额度
        self._release_reservation(order)
        self.account.apply_fill(fill)
        order.apply_fill(quantity, price, fees)

        # 部分成交时，为剩余数量重新冻结
        if order.is_open and order.side is Side.BUY:
            try:
                refreeze = self.cost_model.estimate_buy_cost(price, order.remaining)
                self.account.freeze(refreeze)
                order.meta["frozen_cash"] = refreeze
            except Exception:  # noqa: BLE001 - 资金不够就让剩余部分挂到过期
                order.expire("剩余数量资金不足")
        if order.is_open and order.side is Side.SELL:
            order.meta["frozen_qty"] = order.remaining

        # 跳空缩减过的单：剩余数量在当前价位本来就买不起，继续挂只会白占冻结额度，
        # 直接终止（已成交部分保留）。不这样做的话，剩余资金会一直锁到日终。
        if outcome.cash_capped and order.is_open:
            order.expire(
                f"跳空保护：剩余 {order.remaining:.0f} 股按当前价已无力买入，本单终止"
            )
            self._release_reservation(order)

        self._fills.append(fill)
        self._emit_fill(fill)
        if order.is_final:
            self._emit_order(order)
        return fill

    # ------------------------------------------------------------------ #
    # 日终：未成交的 DAY 单过期（GTC 保留到下一次日终）
    # ------------------------------------------------------------------ #
    def end_of_day(self, now: datetime | None = None) -> None:
        """日终清理：DAY 单过期；GTC 单超过 ``max_order_age_days`` 也过期。

        ``now`` 是行情时钟的当日时刻（回测用），不传时用墙钟（纸盘兼容）。
        """
        moment = now or utcnow()
        for order in list(self._live):
            if not order.is_open:
                continue
            if order.tif is TimeInForce.DAY:
                order.expire("当日未成交")
                self._release_reservation(order)
                self._drop(order)
                self._emit_order(order)
                continue
            if self.max_order_age_days > 0:
                placed = order.created_at or moment
                if (moment - placed).days >= self.max_order_age_days:
                    order.expire(f"挂单超过 {self.max_order_age_days} 天未成交")
                    self._release_reservation(order)
                    self._drop(order)
                    self._emit_order(order)
                    self._expired_by_age += 1
                    log.info("GTC 挂单超龄撤销 %s %s", order.symbol, order.id)

    @property
    def frozen_summary(self) -> dict:
        """冻结资金的可观测快照：多少被锁、被几张单锁的、最长挂了多久。"""
        by_symbol: dict[str, float] = {}
        oldest_days = 0
        for order in self.open_orders:
            frozen = float(order.meta.get("frozen_cash", 0.0) or 0.0)
            if frozen:
                by_symbol[order.symbol] = by_symbol.get(order.symbol, 0.0) + frozen
            if order.created_at:
                oldest_days = max(oldest_days, (utcnow() - order.created_at).days)
        return {
            "frozen_cash": round(self.account.frozen_cash, 2),
            "available_cash": round(self.account.available_cash, 2),
            "open_orders": len(self.open_orders),
            "by_symbol": {k: round(v, 2) for k, v in by_symbol.items()},
            "oldest_order_days": oldest_days,
            "expired_by_age": self._expired_by_age,
        }

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _validate(self, order: Order) -> str | None:
        if order.quantity <= 0:
            return "委托数量必须大于 0"
        if order.order_type is OrderType.LIMIT and (not order.limit_price or order.limit_price <= 0):
            return "限价单必须提供有效限价"
        rules = rules_for(order.symbol)
        if (
            rules.lot_size > 1
            and order.side is Side.BUY
            and abs(order.quantity - round_down_lot(order.quantity, order.symbol)) > 1e-9
        ):
            return f"买入数量须为 {rules.lot_size} 股的整数倍"
        pos = self.account.get_position(order.symbol)
        # 只有「这一单把可卖仓位全卖光」才算完整平仓，才允许零股尾巴。
        # 用 pos.available 而非 pos.quantity：T+1 市场里当日买入的股份不可卖，
        # 拿 quantity 判断会让"卖出可卖部分"被误判为清仓而放行非法零股。
        full_exit = order.side is Side.SELL and abs(order.quantity - pos.available) <= 1e-9
        if not is_valid_lot(order.quantity, order.symbol, is_full_exit=full_exit):
            return "委托数量不合法"

        if order.side is Side.BUY:
            need = self._estimate_cash(order)
            if need > self.account.available_cash + 1e-6:
                return f"可用资金不足：需要 {need:.2f}，可用 {self.account.available_cash:.2f}"
        else:
            sellable = self._sellable(order.symbol, exclude_id=order.id)
            if order.quantity > sellable + 1e-9:
                return f"可卖数量不足：可卖 {sellable}，委托 {order.quantity}"
        return None

    def _estimate_cash(self, order: Order) -> float:
        price = order.limit_price or self.account.last_price(order.symbol)
        if not price or price <= 0:
            # 没有参考价时按限价或 1 元兜底，实际成交后再校正
            price = order.limit_price or 1.0
        # 市价单上浮 2% 作为冻结缓冲，防止开盘跳空导致穿仓
        buffer = 1.0 if order.order_type is OrderType.LIMIT else 1.02
        return self.cost_model.estimate_buy_cost(price * buffer, order.quantity)

    def _sellable(self, symbol: str, exclude_id: str | None = None) -> float:
        """可卖数量 = T+1 可用 - 在途卖单（排除正在校验的这一单）。"""
        pos = self.account.get_position(symbol)
        pending = sum(
            o.quantity - o.filled_quantity
            for o in self.open_orders
            if o.symbol == symbol and o.side is Side.SELL and o.id != exclude_id
        )
        return max(0.0, pos.available - pending)

    def _release_reservation(self, order: Order) -> None:
        frozen = float(order.meta.pop("frozen_cash", 0.0) or 0.0)
        if frozen:
            self.account.unfreeze(frozen)
        order.meta.pop("frozen_qty", None)

    def _drop(self, order: Order) -> None:
        if order in self._live:
            self._live.remove(order)
        if order in self._queued:
            self._queued.remove(order)

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self._queued.clear()
        self._live.clear()
        self._orders.clear()
        self._fills.clear()
        self._last_bars.clear()

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "open_orders": len(self.open_orders),
            "queued_orders": len(self._queued),
            "fills": len(self._fills),
            "frozen_cash": round(self.account.frozen_cash, 2),
        }


__all__ = ["Broker", "SimBroker"]
