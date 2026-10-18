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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd

from ..core.clock import Clock
from ..core.events import EventBus
from ..core.portfolio import Account
from ..core.types import AccountSnapshot, Bar, Fill, Order, utcnow
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
    ) -> None:
        self.account = account
        self.broker = broker
        self.risk = risk
        self.clock = clock
        self.bus = bus
        self.frames = dict(frames)
        self.recorder = recorder
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

    # ------------------------------------------------------------------ #
    def begin(self) -> None:
        self.stats.started_at = datetime.now()
        self.bus.emit_status("running", "行情开始", source=self.strategy.strategy_id)
        self.strategy.on_start()

    def step(
        self,
        moment: datetime,
        bars: Mapping[str, Bar],
        indexes: Mapping[str, int] | None = None,
    ) -> AccountSnapshot:
        """推进一个时间步。``bars`` 可以只有一个标的（纸盘），也可以是多标的（回测）。"""
        self.stats.steps += 1
        setter = getattr(self.clock, "set", None)
        if callable(setter):
            setter(moment)

        today = moment.date()
        if self._last_session_date != today:
            self._last_session_date = today
            self.account.on_new_session()  # T+1 解冻

        # 1) 撮合昨日订单
        for bar in bars.values():
            self.broker.on_bar(bar)
            self.bus.emit_bar(bar, source=self.strategy.strategy_id)

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

        # 4) 日终清理
        self.broker.end_of_day()
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

    @property
    def fills(self) -> list[Fill]:
        return list(self.broker.fills)

    @property
    def orders(self) -> list[Order]:
        return list(self.broker.all_orders)


def make_snapshot_dict(snapshot: AccountSnapshot) -> dict[str, Any]:
    return snapshot.to_dict()


__all__ = ["SessionStats", "TradingSession", "bar_from_frame", "make_snapshot_dict"]
