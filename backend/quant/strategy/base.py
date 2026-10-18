"""策略基类与运行上下文。

策略作者只需要关心三件事：**看到的数据、下的单、拿到的成交**。
账户、撮合、风控、费用、持久化全部由框架处理。

写一个策略::

    @register_strategy
    class MyStrategy(Strategy):
        strategy_id = "my"
        display_name = "我的策略"
        param_specs = (ParamSpec("lookback", "int", 20, 2, 250, description="回看天数"),)

        def on_bar(self, bar):
            hist = self.history(bar.symbol, self.params["lookback"])
            if len(hist) < self.params["lookback"]:
                return
            if hist["close"].iloc[-1] > hist["close"].mean() and self.position(bar.symbol).is_flat:
                self.buy(bar.symbol, percent=0.9)

把类放进 ``quant.strategy.builtin`` 包内（或调用 :func:`register_strategy`），
API 与前端会自动发现它，**不需要改任何注册表或路由代码**。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping

import pandas as pd

from ..core.clock import Clock
from ..core.events import EventBus, EventType
from ..core.portfolio import Account
from ..core.types import Bar, Fill, Order, OrderType, Position, Side
from ..execution.broker import Broker
from ..execution.rules import round_down_lot

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 参数描述
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """一个可调参数。前端据此自动渲染表单并做校验。"""

    name: str
    type: str = "float"  # int | float | bool | str | choice | json
    default: Any = 0.0
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    description: str = ""

    def coerce(self, value: Any) -> Any:
        """把前端传来的字符串/数字转成正确类型，越界则夹紧。"""
        if self.type == "int":
            out = int(float(value))
        elif self.type == "float":
            out = float(value)
        elif self.type == "bool":
            out = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
        elif self.type == "choice":
            out = value
            if self.choices and out not in self.choices:
                return self.default
        elif self.type == "json":
            # 结构化参数（如条件拼装 spec）：dict/list 原样通过，字符串按 JSON 解析
            if isinstance(value, str):
                return json.loads(value)
            return value
        else:
            out = str(value)
        if self.type in ("int", "float"):
            if self.min is not None:
                out = max(self.min, out)
            if self.max is not None:
                out = min(self.max, out)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "choices": list(self.choices),
            "description": self.description,
        }


# --------------------------------------------------------------------------- #
# 运行上下文
# --------------------------------------------------------------------------- #


class StrategyContext:
    """策略与框架之间的唯一接口。

    引擎在推进每一根 bar 之前调用 :meth:`seek`，把内部游标移到当前 bar，
    因此 :meth:`history` 天然不可能看到未来数据。
    """

    def __init__(
        self,
        account: Account,
        broker: Broker,
        clock: Clock,
        frames: Mapping[str, pd.DataFrame],
        bus: EventBus,
        strategy_name: str = "",
        risk: Any = None,
    ) -> None:
        self.account = account
        self.broker = broker
        self.clock = clock
        self.bus = bus
        self.strategy_name = strategy_name
        self.risk = risk
        self._frames: dict[str, pd.DataFrame] = dict(frames)
        self._index: dict[str, int] = {}
        self._current_dt: datetime | None = None
        self.state: dict[str, Any] = {}  # 策略可自由使用的持久化黑盒

    # -- 时间 -------------------------------------------------------------- #
    @property
    def now(self) -> datetime:
        return self._current_dt or self.clock.now

    @property
    def today(self) -> date:
        return self.now.date()

    # -- 游标 -------------------------------------------------------------- #
    def seek(self, symbol: str, index: int, dt: datetime | None = None) -> None:
        self._index[symbol] = index
        if dt is not None:
            self._current_dt = dt

    def seek_all(self, indexes: Mapping[str, int], dt: datetime | None = None) -> None:
        self._index.update(indexes)
        if dt is not None:
            self._current_dt = dt

    # -- 行情 -------------------------------------------------------------- #
    def frame(self, symbol: str) -> pd.DataFrame | None:
        return self._frames.get(symbol)

    def cursor(self, symbol: str) -> int:
        return self._index.get(symbol, -1)

    def history(self, symbol: str, n: int | None = None) -> pd.DataFrame:
        """截至**当前 bar（含）**的历史，最多 ``n`` 行。"""
        frame = self._frames.get(symbol)
        if frame is None:
            return pd.DataFrame()
        i = self._index.get(symbol, -1)
        if i < 0:
            return frame.iloc[0:0]
        lo = 0 if n is None else max(0, i - n + 1)
        return frame.iloc[lo : i + 1]

    def history_bars(self, symbol: str, n: int) -> list[Bar]:
        from ..data.base import frame_to_bars

        return frame_to_bars(self.history(symbol, n), symbol)

    def bar(self, symbol: str) -> Bar | None:
        frame = self._frames.get(symbol)
        i = self._index.get(symbol, -1)
        if frame is None or i < 0 or i >= len(frame):
            return None
        row = frame.iloc[i]
        return Bar(
            symbol=symbol,
            dt=pd.Timestamp(row["date"]).to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0) or 0),
            amount=float(row.get("amount", 0) or 0),
            prev_close=_opt_float(row.get("prev_close")),
            limit_up=_opt_float(row.get("limit_up")),
            limit_down=_opt_float(row.get("limit_down")),
        )

    def price(self, symbol: str) -> float:
        bar = self.bar(symbol)
        if bar is not None and bar.close > 0:
            return bar.close
        return self.account.last_price(symbol)

    def symbols(self) -> list[str]:
        return list(self._frames)

    # -- 账户 -------------------------------------------------------------- #
    @property
    def equity(self) -> float:
        return self.account.equity()

    @property
    def cash(self) -> float:
        return self.account.available_cash

    def position(self, symbol: str) -> Position:
        return self.account.get_position(symbol)

    def positions(self) -> list[Position]:
        return list(self.account.holdings())

    def exposure(self) -> float:
        if self.equity <= 0:
            return 0.0
        return self.account.market_value() / self.equity

    # -- 下单 -------------------------------------------------------------- #
    def submit(self, order: Order) -> Order:
        """提交订单：先过风控，再进券商。风控拒绝的订单不会冻结资金。"""
        order.strategy = self.strategy_name

        if self.risk is not None:
            decision = self.risk.pre_trade(
                order,
                self.account,
                now=self.now,
                open_orders=len(self.broker.open_orders),
            )
            if not decision.allowed:
                order.reject(f"[风控:{decision.rule}] {decision.reason}")
                self.broker.record(order)
                self.bus.emit_order(order, source=self.strategy_name)
                return order

        result = self.broker.submit(order)
        self.bus.emit(EventType.SIGNAL, {
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "status": result.status.value,
            "reason": result.reason,
        }, source=self.strategy_name)
        return result

    def cancel(self, order_id: str) -> bool:
        return self.broker.cancel(order_id)

    def cancel_all(self) -> int:
        return self.broker.cancel_all("策略撤单")

    @property
    def open_orders(self) -> list[Order]:
        return self.broker.open_orders

    # -- 日志 -------------------------------------------------------------- #
    def log(self, message: str, level: str = "info") -> None:
        self.bus.emit_log(f"[{self.strategy_name}] {message}", level=level, source=self.strategy_name)
        getattr(log, level, log.info)("%s", message)


def _opt_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 策略基类
# --------------------------------------------------------------------------- #


class Strategy(ABC):
    """所有策略的基类。

    子类通过类属性声明元信息与参数，框架据此在 Web 端生成表单，
    无需为每个策略单独写接口。
    """

    strategy_id: str = ""
    display_name: str = ""
    description: str = ""
    category: str = "trend"  # trend | mean_reversion | momentum | benchmark
    param_specs: tuple[ParamSpec, ...] = ()

    def __init__(self, ctx: StrategyContext, params: Mapping[str, Any] | None = None) -> None:
        self.ctx = ctx
        self.params: dict[str, Any] = self.resolve_params(params)

    # -- 参数 -------------------------------------------------------------- #
    @classmethod
    def resolve_params(cls, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = {spec.name: spec.default for spec in cls.param_specs}
        for spec in cls.param_specs:
            if overrides and spec.name in overrides and overrides[spec.name] is not None:
                values[spec.name] = spec.coerce(overrides[spec.name])
        return values

    @classmethod
    def schema(cls) -> dict[str, Any]:
        return {
            "id": cls.strategy_id,
            "name": cls.display_name or cls.strategy_id,
            "description": cls.description,
            "category": cls.category,
            "params": [spec.to_dict() for spec in cls.param_specs],
        }

    # -- 生命周期（子类按需覆盖） ------------------------------------------ #
    def on_start(self) -> None:  # noqa: B027 - 可选钩子
        """回测/纸盘开始时调用一次。"""

    def on_bar(self, bar: Bar) -> None:  # noqa: B027
        """每根 bar 调用。``bar`` 是当前标的当根 bar。"""

    def on_bars(self, bars: Mapping[str, Bar]) -> None:
        """可选：一次拿到当天全部标的的 bar（多标的策略用这个更自然）。"""
        for bar in bars.values():
            self.on_bar(bar)

    def on_order(self, order: Order) -> None:  # noqa: B027
        """订单状态变化（含被拒）。"""

    def on_fill(self, fill: Fill) -> None:  # noqa: B027
        """成交回报。"""

    def on_stop(self) -> None:  # noqa: B027
        """结束时调用一次（可用于清理状态）。"""

    # -- 便捷访问 ---------------------------------------------------------- #
    def history(self, symbol: str, n: int | None = None) -> pd.DataFrame:
        return self.ctx.history(symbol, n)

    def price(self, symbol: str) -> float:
        return self.ctx.price(symbol)

    def position(self, symbol: str) -> Position:
        return self.ctx.position(symbol)

    def positions(self) -> list[Position]:
        return self.ctx.positions()

    @property
    def equity(self) -> float:
        return self.ctx.equity

    @property
    def cash(self) -> float:
        return self.ctx.cash

    def log(self, message: str, level: str = "info") -> None:
        self.ctx.log(message, level)

    def has_open_order(self, symbol: str | None = None) -> bool:
        """是否已有在途委托（避免同一根 bar 重复下单）。"""
        orders = self.ctx.open_orders
        if symbol is None:
            return bool(orders)
        return any(o.symbol == symbol for o in orders)

    # -- 下单助手 ---------------------------------------------------------- #
    def buy(
        self,
        symbol: str,
        *,
        quantity: int | None = None,
        amount: float | None = None,
        percent: float | None = None,
        limit_price: float | None = None,
        tag: str = "",
    ) -> Order | None:
        """买入。``quantity`` / ``amount`` / ``percent`` 三选一，按优先级取。"""
        qty = self._size(symbol, Side.BUY, quantity, amount, percent)
        if qty <= 0:
            return None
        return self._order(symbol, Side.BUY, qty, limit_price, tag)

    def sell(
        self,
        symbol: str,
        *,
        quantity: int | None = None,
        amount: float | None = None,
        percent: float | None = None,
        limit_price: float | None = None,
        close_only: bool = True,
        tag: str = "",
    ) -> Order | None:
        """卖出。默认不裸卖空（A 股融券需单独适配器）。"""
        pos = self.ctx.position(symbol)
        qty = self._size(symbol, Side.SELL, quantity, amount, percent)
        if close_only:
            qty = min(qty, pos.available)
        if qty <= 0:
            return None
        return self._order(symbol, Side.SELL, qty, limit_price, tag)

    def close(self, symbol: str, *, tag: str = "清仓") -> Order | None:
        pos = self.ctx.position(symbol)
        if pos.available <= 0:
            return None
        return self._order(symbol, Side.SELL, pos.available, None, tag)

    def order_target_percent(self, symbol: str, target: float, *, tag: str = "") -> Order | None:
        """调仓到目标权重（占当前权益）。正数买入、0 清仓。"""
        price = self.ctx.price(symbol)
        if price <= 0:
            return None
        target_value = self.equity * target
        pos = self.ctx.position(symbol)
        diff_value = target_value - pos.market_value
        if abs(diff_value) < price * 100:
            return None
        if diff_value > 0:
            return self.buy(symbol, amount=diff_value, tag=tag)
        return self.sell(symbol, quantity=min(round_down_lot(abs(diff_value) / price, symbol), pos.available), tag=tag)

    # -- 内部 -------------------------------------------------------------- #
    def _size(
        self,
        symbol: str,
        side: Side,
        quantity: int | None,
        amount: float | None,
        percent: float | None,
    ) -> int:
        if quantity is not None:
            return int(quantity)
        price = self.ctx.price(symbol)
        if price <= 0:
            return 0
        if amount is not None:
            return round_down_lot(max(0.0, amount) / price, symbol)
        if percent is not None:
            budget = self.equity * max(0.0, min(1.0, percent))
            if side is Side.BUY:
                # 市价买入按开盘价 + 2% 缓冲冻结资金，这里先预留出来，避免"算得刚好却下不进去"
                budget = min(budget, self.cash) / 1.02
            return round_down_lot(budget / price, symbol)
        return 0

    def _order(
        self,
        symbol: str,
        side: Side,
        quantity: int,
        limit_price: float | None,
        tag: str,
    ) -> Order:
        order = Order(
            symbol=symbol,
            side=side,
            quantity=int(quantity),
            order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
            limit_price=float(limit_price) if limit_price else None,
            tag=tag,
            strategy=self.ctx.strategy_name,
        )
        return self.ctx.submit(order)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


__all__ = ["ParamSpec", "Strategy", "StrategyContext"]
