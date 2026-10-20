"""交易会话：回测与纸盘**共用的那一段代码**。

一次 ``step`` 的完整顺序（顺序本身就是防止未来函数的关键）::

    1. 新交易日？→ T+1 解冻
    2. 撮合上一交易日收盘后下达的订单（用当日 bar 的开盘/区间）
    3. 按收盘价重估持仓 → 记录权益快照
    4. 风控下单前检查（在 StrategyContext.submit 内部）
    5. 调用策略 on_bars，产生下一交易日的订单
    6. 日终：未成交的 DAY 单过期

**策略在第 5 步看到的是当日收盘数据，它下的单最早在第 2 步（次日开盘）成交。**
这条纪律比任何参数优化都重要。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from ..core.clock import Clock
from ..core.events import EventBus
from ..core.portfolio import Account
from ..core.types import AccountSnapshot, Bar, Fill, Order
from ..execution.broker import Broker
from ..risk.base import RiskManager
from ..strategy.base import Strategy, StrategyContext


@runtime_checkable
class RunRecorderProtocol(Protocol):
    """运行记录器接口（由存储层实现，引擎只依赖抽象，便于单测替换）。"""

    def record_equity(self, snapshot: AccountSnapshot) -> None: ...

    def record_fill(self, fill: Fill) -> None: ...

    def record_order(self, order: Order) -> None: ...

    def record_log(self, message: str, level: str = "info") -> None: ...


log = logging.getLogger(__name__)


def bar_from_frame(frame: pd.DataFrame, index: int, symbol: str) -> Bar:
    """从标准化 DataFrame 的第 ``index`` 行构造 Bar。"""
    row = frame.iloc[index]
    prev_close = row.get("prev_close")
    limit_up = row.get("limit_up")
    limit_down = row.get("limit_down")
    return Bar(
        symbol=symbol,
        dt=pd.Timestamp(row["date"]).to_pydatetime(),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0) or 0),
        amount=float(row.get("amount", 0) or 0),
        prev_close=_opt(prev_close),
        limit_up=_opt(limit_up),
        limit_down=_opt(limit_down),
    )


def _opt(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class SessionStats:
    steps: int = 0
    orders: int = 0
    rejected: int = 0
    fills: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TradingSession:
    """持有账户、券商、风控、策略，并驱动它们按统一的时序运行。"""

    def __init__(
        self,
        *,
        strategy_cls: type[Strategy],
        strategy_params: Mapping[str, Any] | None,
        account: Account,
        broker: Broker,
        risk: RiskManager | None,
        clock: Clock,
        bus: EventBus,
        frames: Mapping[str, pd.DataFrame],
        strategy_name: str = "",
        recorder: RunRecorderProtocol | None = None,
        auto_end_of_day: bool = True,
    ) -> None:
        self.account = account
        self.broker = broker
        self.risk = risk
        self.clock = clock
        self.bus = bus
        self.frames = dict(frames)
        self.recorder = recorder
        self.auto_end_of_day = auto_end_of_day
        self.stats = SessionStats()

        self.ctx = StrategyContext(
            account=account,
            broker=broker,
            clock=clock,
            frames=self.frames,
            bus=bus,
            strategy_name=strategy_name or strategy_cls.strategy_id,
            risk=risk,
        )
        self.strategy = strategy_cls(self.ctx, strategy_params)
        self.equity_curve: list[dict[str, Any]] = []
        self._last_session_date = None
        self._order_states: dict[str, str] = {}
        self._pending_switch: tuple[type[Strategy], dict[str, Any], bool] | None = None

    # ------------------------------------------------------------------ #
    def begin(self) -> None:
        self.stats.started_at = datetime.now()
        self.bus.emit_status("running", "行情开始", source=self.strategy.strategy_id)
        self.strategy.on_start()

    @property
    def current_strategy_id(self) -> str:
        return self.strategy.strategy_id

    def request_switch(
        self,
        strategy_cls: type[Strategy],
        params: Mapping[str, Any] | None = None,
        *,
        cancel_orders: bool = True,
    ) -> None:
        """请求在**下一轮 step 开始时**切换策略。

        只做一次属性赋值，HTTP 线程可以安全调用；真正的替换发生在引擎线程，
        避免与 ``on_bars`` 并发修改策略实例。
        """
        self._pending_switch = (strategy_cls, dict(params or {}), bool(cancel_orders))

    def _apply_pending_switch(self) -> None:
        pending = self._pending_switch
        if pending is None:
            return
        self._pending_switch = None
        strategy_cls, params, cancel_orders = pending
        old = self.strategy
        old_id = old.strategy_id
        try:
            old.on_stop()
        except Exception:  # noqa: BLE001 - 旧策略清理失败不应阻断切换
            log.exception("旧策略 on_stop 异常 %s", old_id)

        cancelled = 0
        if cancel_orders:
            for order in list(self.broker.open_orders):
                if order.strategy == old_id and self.broker.cancel(order.id):
                    cancelled += 1

        new_strategy = strategy_cls(self.ctx, params)
        self.ctx.strategy_name = new_strategy.strategy_id
        self.strategy = new_strategy
        try:
            new_strategy.on_start()
        except Exception:  # noqa: BLE001
            log.exception("新策略 on_start 异常 %s", new_strategy.strategy_id)

        suffix = f"（已撤销旧挂单 {cancelled} 笔）" if cancelled else ""
        self.bus.emit_log(f"策略切换：{old_id} → {new_strategy.strategy_id}{suffix}", source="session")
        self.bus.emit_status(
            "running", f"策略已切换为 {new_strategy.strategy_id}", source=new_strategy.strategy_id
        )

    def step(
        self,
        moment: datetime,
        bars: Mapping[str, Bar],
        indexes: Mapping[str, int] | None = None,
    ) -> AccountSnapshot:
        """推进一个时间步。``bars`` 可以只有一个标的（纸盘），也可以是多标的（回测）。"""
        self.stats.steps += 1
        self._apply_pending_switch()
        setter = getattr(self.clock, "set", None)
        if callable(setter):
            setter(moment)

        today = moment.date()
        if self._last_session_date != today:
            self._last_session_date = today
            self.account.on_new_session()  # T+1 解冻

        # 1) 撮合昨日订单（实盘由交易所回报驱动，on_bar 只交付已同步到的成交）
        for bar in bars.values():
            fills = self.broker.on_bar(bar) or []
            for fill in fills:
                self.strategy.on_fill(fill)
            self.bus.emit_bar(bar, source=self.strategy.strategy_id)
        self._notify_orders()

        # 2) 收盘重估 + 权益快照
        for bar in bars.values():
            self.account.mark_price(bar.symbol, bar.close)
        snapshot = self.account.snapshot(moment)
        self.equity_curve.append(snapshot.to_dict())
        self.bus.emit_account(snapshot, source=self.strategy.strategy_id)

        # 3) 策略决策
        if indexes:
            self.ctx.seek_all(indexes, moment)
        self.strategy.on_bars(bars)

        # 4) 日终清理（实时模式由引擎在真正跨日时调用，避免每轮轮询撤掉当日挂单）
        if self.auto_end_of_day:
            self.broker.end_of_day()
        self._notify_orders()
        self._sync_stats()
        if self.recorder is not None:
            self.recorder.record_equity(snapshot)
        return snapshot

    def finish(self) -> None:
        self.stats.finished_at = datetime.now()
        self.strategy.on_stop()
        self.broker.cancel_all("回测结束")
        self.bus.emit_status("finished", "运行结束", source=self.strategy.strategy_id)

    # ------------------------------------------------------------------ #
    def _sync_stats(self) -> None:
        orders = self.broker.all_orders
        self.stats.orders = len(orders)
        self.stats.rejected = sum(1 for o in orders if o.status.value == "rejected")
        self.stats.fills = len(self.broker.fills)

    def _notify_orders(self) -> None:
        """订单状态变化回调策略 ``on_order``（同一个状态只通知一次）。"""
        for order in self.broker.all_orders:
            status = order.status.value
            if self._order_states.get(order.id) == status:
                continue
            self._order_states[order.id] = status
            try:
                self.strategy.on_order(order)
            except Exception:  # noqa: BLE001 - 策略回调异常不应打断引擎
                log.exception("策略 on_order 回调异常 %s", order.id)

    @property
    def fills(self) -> list[Fill]:
        return list(self.broker.fills)

    @property
    def orders(self) -> list[Order]:
        return list(self.broker.all_orders)


__all__ = ["SessionStats", "TradingSession", "bar_from_frame"]
