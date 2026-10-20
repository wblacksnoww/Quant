"""实盘券商：把适配器的回报翻译成账户记账。

与 :class:`~quant.execution.broker.SimBroker` 的分工：

* SimBroker 自己撮合；LiveBroker **不撮合**，成交只来自交易所回报；
* 下单/撤单经 :class:`~quant.execution.adapters.base.BrokerAdapter`；
* 每轮 :meth:`sync` 拉取累计成交与订单状态，转换成增量成交记账。

交易所返回的是**累计口径**（累计成交量/累计均价/累计费用），这里计算增量，
因此重复调用 ``sync()`` 不会重复记账。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.events import EventBus
from ..core.portfolio import Account
from ..core.types import Bar, FeeBreakdown, Fill, Order, OrderType, Side, TimeInForce, utcnow
from .adapters.base import AdapterError, AdapterFill, AdapterStatus, BrokerAdapter
from .broker import Broker
from .cost import CostModel
from .rules import is_valid_lot, rules_for

log = logging.getLogger(__name__)

_TERMINAL_STATUS = {
    "filled": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
    "expired": "expired",
}


class LiveBroker(Broker):
    """真实券商账户的本地镜像。

    资金/持仓以交易所为准；本地 Account 只用于策略读取与风控前置校验，
    :meth:`reconcile` 可随时比对两边并把差异写进日志（不自动改账）。
    """

    def __init__(
        self,
        account: Account,
        adapter: BrokerAdapter,
        bus: EventBus | None = None,
        *,
        cost_model: CostModel | None = None,
        name: str = "live",
    ) -> None:
        super().__init__(account, bus, name=name)
        self.adapter = adapter
        self.cost_model = cost_model or CostModel.for_market("CN")
        self._by_external: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._last_quotes: dict[str, float] = {}
        self._pending_fills: list[Fill] = []

    # ------------------------------------------------------------------ #
    # 启停与对账
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        self.adapter.connect()

    def bind_account(self, symbols: list[str], prices: dict[str, float] | None = None) -> dict[str, Any]:
        """启动时用交易所资金/持仓初始化本地镜像。

        外部持仓的成本价无法从交易所获得，按 ``prices``（缺省 0）记成本，
        并打上 ``external`` 标记——策略读到的是真实数量，但盈亏口径要谨慎。
        """
        prices = prices or {}
        balance = self.adapter.fetch_balance()
        self.account.cash = float(balance.cash)
        self.account.initial_cash = float(balance.cash) if balance.cash > 0 else self.account.initial_cash
        self.account.frozen_cash = 0.0

        remote = self.adapter.fetch_positions(symbols)
        for symbol, qty in remote.items():
            if qty <= 0:
                continue
            pos = self.account.get_position(symbol)
            pos.quantity = float(qty)
            pos.available = float(qty)  # 外部持仓视为已过 T+1
            price = prices.get(symbol, 0.0)
            pos.avg_cost = float(price)
            pos.last_price = float(price)
            pos.opened_at = utcnow()
        return {
            "cash": self.account.cash,
            "currency": balance.currency,
            "positions": {s: q for s, q in remote.items() if q > 0},
        }

    def reconcile(self) -> dict[str, Any]:
        """本地镜像 vs 交易所：只报告差异，不自动改账。"""
        balance = self.adapter.fetch_balance()
        symbols = [o.symbol for o in self.open_orders] or list(self.account.positions)
        remote = self.adapter.fetch_positions(list(dict.fromkeys(symbols)))
        cash_diff = float(balance.cash) - self.account.cash
        position_diffs = []
        for symbol in set(remote) | {s for s, p in self.account.positions.items() if not p.is_flat}:
            local = self.account.get_position(symbol).quantity
            remote_qty = float(remote.get(symbol, 0.0))
            if abs(local - remote_qty) > 1e-8:
                position_diffs.append({"symbol": symbol, "local": local, "remote": remote_qty})
        ok = abs(cash_diff) <= 1e-6 and not position_diffs
        if not ok:
            self.bus.emit_log(
                f"实盘对账存在差异：现金差 {cash_diff:.4f}，持仓差异 {len(position_diffs)} 项",
                level="warning",
                source=self.name,
            )
        return {
            "ok": ok,
            "cash_local": self.account.cash,
            "cash_remote": balance.cash,
            "cash_diff": cash_diff,
            "positions": position_diffs,
        }

    # ------------------------------------------------------------------ #
    # 提交与撤销
    # ------------------------------------------------------------------ #
    def submit(self, order: Order) -> Order:
        self._orders[order.id] = order
        reason = self._validate(order)
        if reason:
            order.reject(reason)
            self._emit_order(order)
            return order

        if order.side is Side.BUY:
            frozen = self._estimate_cash(order)
            try:
                self.account.freeze(frozen)
            except Exception as exc:  # noqa: BLE001
                order.reject(str(exc))
                self._emit_order(order)
                return order
            order.meta["frozen_cash"] = frozen
        else:
            order.meta["frozen_qty"] = order.quantity

        try:
            external_id = self.adapter.place_order(order)
        except AdapterError as exc:
            self._release_reservation(order)
            order.reject(f"交易所拒单: {exc}")
            self._emit_order(order)
            return order
        except Exception as exc:  # noqa: BLE001 - 适配器意外异常同样视为拒单
            self._release_reservation(order)
            order.reject(f"下单失败: {type(exc).__name__}: {exc}")
            self._emit_order(order)
            return order

        order.meta["external_id"] = str(external_id)
        self._by_external[str(external_id)] = order
        order.mark_submitted()
        self._emit_order(order)
        log.info(
            "实盘委托已提交 %s %s %s -> %s",
            order.symbol,
            order.side.value,
            order.quantity,
            external_id,
        )
        return order

    def cancel(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_open:
            return False
        external_id = str(order.meta.get("external_id") or "")
        if external_id:
            try:
                acked = self.adapter.cancel_order(external_id, order.symbol)
            except AdapterError as exc:
                log.warning("撤单失败 %s: %s", order.symbol, exc)
                return False
            except Exception:  # noqa: BLE001
                log.exception("撤单异常 %s", order.symbol)
                return False
            if not acked:
                return False
        order.cancel()
        self._release_reservation(order)
        self._emit_order(order)
        return True

    # ------------------------------------------------------------------ #
    # 行情推进：实盘不做本地撮合
    # ------------------------------------------------------------------ #
    def on_bar(self, bar: Bar) -> list[Fill]:
        """实盘不做本地撮合；把上一轮 ``sync()`` 收到的成交交给会话/策略。"""
        self._last_quotes[bar.symbol] = bar.close
        self.account.mark_price(bar.symbol, bar.close)
        pending = self._pending_fills
        self._pending_fills = []
        return pending

    # ------------------------------------------------------------------ #
    # 回报同步
    # ------------------------------------------------------------------ #
    def sync(self) -> list[Fill]:
        """拉取交易所回报并记账，返回本次新增的成交。"""
        try:
            events = self.adapter.sync()
        except AdapterError as exc:
            self.bus.emit_log(f"实盘回报同步失败: {exc}", level="error", source=self.name)
            return []
        except Exception as exc:  # noqa: BLE001
            log.exception("实盘回报同步异常")
            self.bus.emit_log(
                f"实盘回报同步异常: {type(exc).__name__}: {exc}", level="error", source=self.name
            )
            return []

        fills: list[Fill] = []
        for event in events:
            order = self._by_external.get(event.external_id)
            if order is None:
                log.debug("忽略未知委托的回报 external_id=%s", event.external_id)
                continue
            try:
                if isinstance(event, AdapterFill):
                    fill = self._apply_cumulative_fill(order, event)
                    if fill is not None:
                        fills.append(fill)
                elif isinstance(event, AdapterStatus):
                    self._apply_status(order, event)
            except Exception:  # noqa: BLE001 - 单笔回报异常不应中断整轮同步
                log.exception("处理委托 %s 的回报失败", event.external_id)
                self.bus.emit_log(
                    f"处理委托回报失败 external_id={event.external_id}",
                    level="error",
                    source=self.name,
                )
                continue
        self._pending_fills.extend(fills)
        return fills

    def _apply_cumulative_fill(self, order: Order, event: AdapterFill) -> Fill | None:
        local_filled = order.filled_quantity
        cum_filled = float(event.filled)
        delta_qty = cum_filled - local_filled
        if delta_qty <= 1e-12:
            return None

        prev_notional = order.avg_fill_price * local_filled
        delta_notional = cum_filled * float(event.avg_price) - prev_notional
        price = delta_notional / delta_qty if delta_qty > 0 else float(event.avg_price)
        if price <= 0:
            price = float(event.avg_price)

        prev_fee = order.fees.total
        delta_fee = max(0.0, float(event.fee) - prev_fee)
        fees = FeeBreakdown(commission=delta_fee)

        realized = self.account.realized_pnl_for(order.symbol, order.side, price, delta_qty, fees.total)
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=delta_qty,
            price=price,
            dt=event.dt or utcnow(),
            fees=fees,
            strategy=order.strategy,
            tag=order.tag,
            realized_pnl=realized,
        )

        self._release_reservation(order)
        self.account.apply_fill(fill)
        order.apply_fill(delta_qty, price, fees)

        if order.is_open and order.side is Side.BUY:
            try:
                refreeze = self.cost_model.estimate_buy_cost(price, order.remaining)
                self.account.freeze(refreeze)
                order.meta["frozen_cash"] = refreeze
            except Exception:  # noqa: BLE001 - 剩余资金不足就交给交易所自然处理
                pass
        if order.is_open and order.side is Side.SELL:
            order.meta["frozen_qty"] = order.remaining

        self._fills.append(fill)
        self._emit_fill(fill)
        if order.is_final:
            self._emit_order(order)
            self._forget(order)
        return fill

    def _forget(self, order: Order) -> None:
        """终态后不再跟踪外部委托号（迟到的重复回报会被安全忽略）。"""
        external_id = str(order.meta.get("external_id") or "")
        if external_id:
            self._by_external.pop(external_id, None)

    def _apply_status(self, order: Order, event: AdapterStatus) -> None:
        status = _TERMINAL_STATUS.get(event.status)
        if status is None:
            return  # open 状态无需处理
        if order.is_final:
            return
        if status == "cancelled":
            order.cancel(event.reason or "交易所撤单")
        elif status == "rejected":
            order.reject(event.reason or "交易所拒单")
        elif status == "expired":
            order.expire(event.reason or "交易所过期")
        elif status == "filled" and order.remaining > 0:
            # 状态已终态但成交回报尚未到：等下一轮 sync 补成交
            log.info("委托 %s 已 filled 但成交回报未到，等待下一轮同步", order.id)
            return
        self._release_reservation(order)
        self._emit_order(order)
        self._forget(order)

    # ------------------------------------------------------------------ #
    # 日终：DAY 单撤单，GTC 保留
    # ------------------------------------------------------------------ #
    def end_of_day(self) -> None:
        for order in list(self.open_orders):
            if order.tif is not TimeInForce.DAY:
                continue
            self.cancel(order.id)

    # ------------------------------------------------------------------ #
    # 校验与内部
    # ------------------------------------------------------------------ #
    def _validate(self, order: Order) -> str | None:
        if order.quantity <= 0:
            return "委托数量必须大于 0"
        if order.order_type is OrderType.LIMIT and (not order.limit_price or order.limit_price <= 0):
            return "限价单必须提供有效限价"
        rules = rules_for(order.symbol)
        pos = self.account.get_position(order.symbol)
        full_exit = order.side is Side.SELL and abs(order.quantity - pos.quantity) <= 1e-9
        if not is_valid_lot(order.quantity, order.symbol, is_full_exit=full_exit):
            return f"委托数量不合法（{rules.name} 最小单位 {rules.lot_size}）"
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
        price = (
            order.limit_price
            or self.account.last_price(order.symbol)
            or self._last_quotes.get(order.symbol, 0.0)
        )
        if not price or price <= 0:
            price = order.limit_price or 1.0
        buffer = 1.0 if order.order_type is OrderType.LIMIT else 1.02
        return self.cost_model.estimate_buy_cost(price * buffer, order.quantity)

    def _sellable(self, symbol: str, exclude_id: str | None = None) -> float:
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

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter": self.adapter.name,
            "sandbox": self.adapter.sandbox,
            "open_orders": len(self.open_orders),
            "fills": len(self._fills),
            "frozen_cash": round(self.account.frozen_cash, 2),
        }


__all__ = ["LiveBroker"]
