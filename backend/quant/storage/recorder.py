"""运行记录器：把引擎事件写进 ``runs`` 表。

与仓储层分开的原因：它在后台线程里运行，自带独立短事务，并且持有运行时
缓冲（权益/成交/委托/日志），生命周期与请求级 Session 完全不同。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..core.types import AccountSnapshot, Fill, Order
from .db import Database
from .models import utcnow_naive
from .repositories.runs import RunRepository

log = logging.getLogger(__name__)


class RunRecorder:
    """把引擎事件写进 ``runs`` 表（实现 :class:`~quant.engine.session.RunRecorderProtocol`）。

    所有写操作都用**独立的短事务**，避免长时间占用写锁影响 API。
    内存里同时保留一份，``finalize`` 时一次性落库压缩 JSON。
    """

    def __init__(
        self,
        db: Database,
        run_id: str,
        *,
        user_id: int | None = None,
        kind: str = "backtest",
        strategy_id: str = "",
        symbols: Sequence[str] = (),
        config: dict[str, Any] | None = None,
        flush_every: int = 500,
        seed: dict[str, Any] | None = None,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.user_id = user_id
        self.kind = kind
        self.strategy_id = strategy_id
        self.symbols = list(symbols)
        self.config = config or {}
        self.flush_every = max(1, flush_every)

        self.equity: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.logs: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_flush = 0
        self._started = False
        # 从停止处继续：历史曲线/成交/委托先冻结在这里，finalize 时与新一段拼接
        seed = seed or {}
        self.seed_equity: list[dict[str, Any]] = list(seed.get("equity") or [])
        self.seed_trades: list[dict[str, Any]] = list(seed.get("trades") or [])
        self.seed_orders: list[dict[str, Any]] = list(seed.get("orders") or [])
        self.seed_logs: list[dict[str, Any]] = list(seed.get("logs") or [])
        self.seed_elapsed = float(seed.get("elapsed_seconds") or 0.0)

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            with self.db.session() as session:
                repo = RunRepository(session)
                record = repo.get(self.run_id)
                if record is None:
                    repo.create(
                        self.run_id,
                        user_id=self.user_id,
                        kind=self.kind,
                        strategy_id=self.strategy_id,
                        symbols=self.symbols,
                        config=self.config,
                        status="running",
                    )
                else:
                    # 恢复同一 run_id：把旧记录重新置为运行中
                    record.status = "running"
                    record.error = None
                    record.progress = 0.0
                    record.finished_at = None
                    record.elapsed_seconds = self.seed_elapsed
                    if self.config:
                        record.config = self.config
            self._started = True

    # -- 引擎协议 ---------------------------------------------------------- #
    def record_equity(self, snapshot: AccountSnapshot | dict[str, Any]) -> None:
        data = snapshot.to_dict() if isinstance(snapshot, AccountSnapshot) else dict(snapshot)
        with self._lock:
            self.equity.append(data)
        self._maybe_flush()

    def record_fill(self, fill: Fill | dict[str, Any]) -> None:
        data = fill.to_dict() if isinstance(fill, Fill) else dict(fill)
        with self._lock:
            self.fills.append(data)

    def record_fill_event(self, payload: dict[str, Any]) -> None:
        self.record_fill(payload)

    def record_order(self, order: Order | dict[str, Any]) -> None:
        data = order.to_dict() if isinstance(order, Order) else dict(order)
        with self._lock:
            self.orders[data["id"]] = data

    def record_order_event(self, payload: dict[str, Any]) -> None:
        self.record_order(payload)

    def record_log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append({"ts": datetime.utcnow().isoformat(), "message": message, "level": level})
            if len(self.logs) > 1000:
                del self.logs[: len(self.logs) - 1000]

    def record_log_event(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.logs.append(dict(payload))
            if len(self.logs) > 1000:
                del self.logs[: len(self.logs) - 1000]

    # ------------------------------------------------------------------ #
    def update_progress(self, progress: float, message: str = "") -> None:
        with self._lock:
            should = int(progress * 100) % 5 == 0
        if not should:
            return
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.progress = float(progress)
                    if message and len(self.logs) < 1000:
                        self.record_log(message, "progress")
        except Exception:  # noqa: BLE001 - 进度写失败不应中断回测
            log.debug("进度更新失败", exc_info=True)

    def touch(self) -> None:
        """纸盘定期心跳，让前端知道还活着。"""
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.progress = 1.0
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    def _maybe_flush(self) -> None:
        if len(self.equity) - self._last_flush < self.flush_every:
            return
        self._last_flush = len(self.equity)
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.equity_curve = self.equity[-2000:]
                    record.trades = self.fills[-500:]
                    record.orders_data = list(self.orders.values())[-500:]
        except Exception:  # noqa: BLE001
            log.debug("中间结果落库失败", exc_info=True)

    # ------------------------------------------------------------------ #
    def finalize(self, result: Any) -> None:
        """把最终结果写入 runs 表。``result`` 可以是 EngineResult 或其 dict。"""
        payload = result.to_dict(max_points=5000) if hasattr(result, "to_dict") else dict(result)
        with self._lock:
            equity = self.seed_equity + list(payload.get("equity_curve") or self.equity)
            trades = self.seed_trades + list(payload.get("trades") or self.fills)
            merged_orders: dict[str, dict[str, Any]] = {
                str(item.get("id")): item for item in self.seed_orders if item.get("id")
            }
            for item in payload.get("orders") or list(self.orders.values()):
                if item.get("id"):
                    merged_orders[str(item["id"])] = item
            orders = list(merged_orders.values()) or list(self.orders.values())
            positions = payload.get("positions") or []
            logs = (self.seed_logs + list(payload.get("logs") or self.logs))[-1000:]
            metrics = payload.get("metrics") or {}
            status = payload.get("status", "finished")
            error = payload.get("error")
            elapsed = self.seed_elapsed + float(payload.get("elapsed_seconds") or 0.0)

        try:
            with self.db.session() as session:
                repo = RunRepository(session)
                record = repo.get(self.run_id)
                if record is None:
                    record = repo.create(
                        self.run_id,
                        user_id=self.user_id,
                        kind=self.kind,
                        strategy_id=self.strategy_id,
                        symbols=self.symbols,
                        config=self.config,
                        status=status,
                    )
                record.status = status
                record.metrics = metrics
                record.equity_curve = equity
                record.trades = trades
                record.orders_data = orders
                record.positions = positions
                record.logs = logs[-1000:]
                record.error = error
                record.progress = 1.0 if status in ("finished", "failed", "cancelled") else record.progress
                record.elapsed_seconds = elapsed
                record.finished_at = utcnow_naive()
        except Exception:  # noqa: BLE001
            log.exception("运行结果落库失败 run_id=%s", self.run_id)


__all__ = ["RunRecorder"]
