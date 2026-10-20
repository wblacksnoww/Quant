"""纸盘引擎：真实时钟 + 实时行情 + 与回测完全相同的交易组件。

两种模式：

``live``
    盘中轮询实时价（akshare 快照），非交易时段休眠。适合盯盘。
``replay``
    按倍速重放最近的历史日线，不需要等开盘，适合演示与验收。

无论哪种模式，策略、风控、费用、撮合走的都是和回测同一套代码——
这正是"回测能跑，纸盘就能跑"的保证。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from ..core.clock import CN_TZ, RealClock, SimClock
from ..core.events import EventBus, EventRecorder, EventType
from ..core.portfolio import Account
from ..core.types import Bar, EngineStatus, FeeBreakdown, Fill, Position, Side
from ..data.calendar import is_market_open
from ..data.store import MarketDataService
from ..execution.broker import SimBroker
from ..execution.cost import CostModel
from ..execution.matching import MatchingEngine
from ..risk.base import RiskManager
from ..strategy.base import Strategy
from ..strategy.registry import get_strategy
from .base import BaseEngine, EngineConfig, EngineResult
from .live_frames import append_today_row, update_today_row
from .session import TradingSession, bar_from_frame

log = logging.getLogger(__name__)


@dataclass
class PaperResumeState:
    """从历史会话恢复纸盘所需的账户与进度（停止后继续用）。"""

    cash: float
    positions: list[dict[str, Any]] = field(default_factory=list)
    last_date: date | None = None  # replay：已推进到的交易日；None 表示实时模式
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    turnover: float = 0.0
    trade_count: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)  # 用于精确重建现金/持仓


@dataclass
class PaperOptions:
    mode: str = "replay"  # live | replay
    poll_interval: float = 5.0  # live 模式轮询秒数
    replay_days: int = 120  # replay 模式回放最近多少个交易日
    replay_interval: float = 0.35  # replay 模式每根 bar 间隔秒数
    respect_market_hours: bool = True


class PaperEngine(BaseEngine):
    """纸盘交易引擎。``run()`` 是阻塞的，由 :class:`PaperRunner` 放到后台线程。"""

    def __init__(
        self,
        config: EngineConfig,
        data: MarketDataService,
        *,
        options: PaperOptions | None = None,
        bus: EventBus | None = None,
        recorder: Any = None,
        resume: PaperResumeState | None = None,
    ) -> None:
        super().__init__(config)
        self.data = data
        self.options = options or PaperOptions()
        self.resume_state = resume
        self.bus = bus or EventBus(f"paper:{config.run_id}")
        self.recorder = recorder
        self._recorder_events = EventRecorder(self.bus, limit=5000)
        self.status = EngineStatus.IDLE
        self.account: Account | None = None
        self.broker: SimBroker | None = None
        self.session: TradingSession | None = None
        self.last_prices: dict[str, float] = {}
        self.last_error: str | None = None
        self.started_at: datetime | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        # 暂停用独立 Event：置位时主循环挂起，会话、账户、策略状态全部保留
        self._paused = threading.Event()

    # ------------------------------------------------------------------ #
    def run(self) -> EngineResult:
        self.status = EngineStatus.PREPARING
        self.started_at = datetime.now(CN_TZ)
        started = time.monotonic()
        result = EngineResult(
            run_id=self.config.run_id,
            strategy_id=self.config.strategy_id,
            status="running",
            config=self._config_dict(),
            started_at=datetime.now(),
        )
        try:
            frames = self._load_frames()
            if not frames:
                raise RuntimeError("纸盘启动失败：没有可用的历史行情")
            session = self._build_session(frames)
            self.session = session
            session.begin()
            self.status = EngineStatus.RUNNING
            self.bus.emit_status("running", "纸盘已启动", source="paper")

            if self.options.mode == "replay":
                self._run_replay(frames, session)
            else:
                self._run_live(frames, session)

            session.finish()
            self.status = EngineStatus.FINISHED if not self.cancelled else EngineStatus.STOPPED
            result.status = self.status.value
        except Exception as exc:  # noqa: BLE001
            log.exception("纸盘运行失败")
            self.status = EngineStatus.FAILED
            self.last_error = f"{type(exc).__name__}: {exc}"
            result.status = "failed"
            result.error = self.last_error
            self.bus.emit_status("failed", self.last_error, source="paper")
        finally:
            result.finished_at = datetime.now()
            result.elapsed_seconds = time.monotonic() - started
            if self.session is not None:
                self._fill_result(result, self.session)
            self._recorder_events.close()
            if self.recorder is not None:
                try:
                    self.recorder.finalize(result)
                except Exception:  # noqa: BLE001
                    log.exception("纸盘结果落库失败")
        return result

    # ------------------------------------------------------------------ #
    def _config_dict(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "strategy_id": cfg.strategy_id,
            "symbols": cfg.symbols,
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "initial_cash": cfg.initial_cash,
            "params": cfg.params,
            "cost": cfg.cost,
            "risk": cfg.risk,
            "mode": self.options.mode,
        }

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        end = self.config.end
        if self.options.mode == "replay":
            # 回放最近 N 个交易日
            probe = self.data.bars(self.config.symbols[0], self.config.start, end)
            tail = probe.tail(self.options.replay_days)
            start = tail["date"].iloc[0].date() if len(tail) else self.config.start
        else:
            start = self.config.start
        for symbol in self.config.symbols:
            try:
                frame = self.data.bars(symbol, start, end)
            except Exception as exc:  # noqa: BLE001
                log.warning("纸盘加载 %s 失败: %s", symbol, exc)
                continue
            if frame is not None and len(frame):
                frames[symbol] = frame.reset_index(drop=True)
        return frames

    def _build_session(self, frames: dict[str, pd.DataFrame]) -> TradingSession:
        cfg = self.config
        cost = CostModel.for_market(cfg.market, cfg.cost)
        account = Account(cfg.initial_cash, name=cfg.run_id)
        if self.resume_state is not None:
            self._restore_account(account)
            self.last_prices = {
                item.symbol: item.last_price
                for item in account.holdings()
                if item.last_price
            }
        else:
            for symbol, frame in frames.items():
                account.mark_price(symbol, float(frame["close"].iloc[0]))

        matching = MatchingEngine(cost, volume_limit_pct=0.0)  # 纸盘不做量能约束
        broker = SimBroker(
            account,
            self.bus,
            matching=matching,
            match_on_submit=True,  # 纸盘市价单立即成交
            name=f"paper:{cfg.run_id}",
        )
        risk = RiskManager.from_config(cfg.risk, bus=self.bus)
        strategy_cls: type[Strategy] = get_strategy(cfg.strategy_id)

        self.account = account
        self.broker = broker
        clock = SimClock(cfg.start) if self.options.mode == "replay" else RealClock()
        return TradingSession(
            strategy_cls=strategy_cls,
            strategy_params=cfg.params,
            account=account,
            broker=broker,
            risk=risk,
            clock=clock,
            bus=self.bus,
            frames=frames,
            strategy_name=cfg.strategy_id,
            recorder=self.recorder,
            # replay 逐日推进，日终即真实日终；live 由引擎在跨日时撤单
            auto_end_of_day=self.options.mode == "replay",
        )

    def _restore_account(self, account: Account) -> None:
        """把上次停止时落库的现金/持仓/统计灌回新账户。"""
        state = self.resume_state
        if state is None:  # pragma: no cover - 调用点已判空
            return
        # 优先按成交流水重建：现金、成本、已实现盈亏都能精确还原；
        # 成交被截断/损坏时退回停止时落库的账户快照。
        if state.trades:
            try:
                for raw in state.trades:
                    account.apply_fill(self._fill_from_record(raw))
                for raw in state.positions:
                    symbol = str(raw.get("symbol") or "")
                    if symbol and raw.get("last_price"):
                        account.mark_price(symbol, float(raw["last_price"]))
                return
            except Exception:  # noqa: BLE001 - 快照兜底，不阻断继续
                log.warning("按成交重建纸盘账户失败，改用持仓快照", exc_info=True)
                account.reset()
        account.cash = float(state.cash)
        account.realized_pnl = float(state.realized_pnl)
        account.total_fees = float(state.total_fees)
        account.turnover = float(state.turnover)
        account.trade_count = int(state.trade_count)
        for raw in state.positions:
            symbol = str(raw.get("symbol") or "")
            quantity = float(raw.get("quantity") or 0.0)
            if not symbol or quantity <= 0:
                continue
            account.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                available=float(raw.get("available") or quantity),
                avg_cost=float(raw.get("avg_cost") or 0.0),
                last_price=float(raw.get("last_price") or 0.0),
                realized_pnl=float(raw.get("realized_pnl") or 0.0),
            )
            if raw.get("last_price"):
                account.mark_price(symbol, float(raw["last_price"]))

    @staticmethod
    def _fill_from_record(raw: dict[str, Any]) -> Fill:
        fees = raw.get("fees") or {}
        raw_dt = raw.get("dt")
        try:
            dt = datetime.fromisoformat(str(raw_dt)) if raw_dt else datetime.now()
        except (TypeError, ValueError):
            dt = datetime.now()
        return Fill(
            order_id=str(raw.get("order_id") or ""),
            symbol=str(raw.get("symbol") or ""),
            side=Side(str(raw.get("side") or "buy")),
            quantity=float(raw.get("quantity") or 0.0),
            price=float(raw.get("price") or 0.0),
            dt=dt,
            fees=FeeBreakdown(
                commission=float(fees.get("commission") or 0.0),
                stamp_tax=float(fees.get("stamp_tax") or 0.0),
                transfer_fee=float(fees.get("transfer_fee") or 0.0),
                slippage=float(fees.get("slippage") or 0.0),
            ),
            id=str(raw.get("id") or ""),
            strategy=str(raw.get("strategy") or ""),
            tag=str(raw.get("tag") or ""),
            realized_pnl=float(raw.get("realized_pnl") or 0.0),
        )

    # ------------------------------------------------------------------ #
    # replay：加速回放历史
    # ------------------------------------------------------------------ #
    def _run_replay(self, frames: dict[str, pd.DataFrame], session: TradingSession) -> None:
        symbols = list(frames)
        index_maps = {s: {d: i for i, d in enumerate(frames[s]["date"].dt.date)} for s in symbols}
        dates = sorted({d for s in symbols for d in index_maps[s]})
        interval = max(0.0, self.options.replay_interval)

        resume_after = self.resume_state.last_date if self.resume_state is not None else None
        for day in dates:
            if self.cancelled:
                return
            if resume_after is not None and day <= resume_after:
                continue
            self._wait_if_paused()
            bars: dict[str, Bar] = {}
            indexes: dict[str, int] = {}
            for symbol in symbols:
                i = index_maps[symbol].get(day)
                if i is None:
                    continue
                bars[symbol] = bar_from_frame(frames[symbol], i, symbol)
                indexes[symbol] = i
            moment = datetime.combine(day, datetime.min.time()).replace(tzinfo=CN_TZ)
            session.step(moment, bars, indexes)
            self.last_prices = {s: b.close for s, b in bars.items()}
            if interval:
                time.sleep(interval)

    # ------------------------------------------------------------------ #
    # live：轮询实时价
    # ------------------------------------------------------------------ #
    def _run_live(self, frames: dict[str, pd.DataFrame], session: TradingSession) -> None:
        symbols = list(frames)
        live_frames: dict[str, pd.DataFrame] = {s: frames[s].copy() for s in symbols}
        current_day: date | None = None
        interval = max(1.0, self.options.poll_interval)

        while not self.cancelled:
            self._wait_if_paused()
            now = datetime.now(CN_TZ)
            today = now.date()

            if self.options.respect_market_hours and not is_market_open(self.config.market, now):
                self.bus.emit_status("idle", "非交易时段，等待开盘", source="paper")
                self._sleep(30)
                continue

            prices = self.data.realtime(symbols)
            if not prices:
                # 数据源不支持实时：退回上一根收盘价并告警（不静默）
                prices = {
                    s: float(live_frames[s]["close"].iloc[-1])
                    for s in symbols
                    if len(live_frames[s])
                }
                self.bus.emit_log("实时行情不可用，使用最近收盘价模拟", level="warning", source="paper")

            # 新交易日：先把昨日的 DAY 挂单过期，再给每个标的追加"今日"的活 bar
            if current_day != today:
                if current_day is not None:
                    session.broker.end_of_day()
                current_day = today
                for symbol in symbols:
                    live_frames[symbol] = self._append_today_row(live_frames[symbol], today, prices.get(symbol))
                session.frames = live_frames
                session.ctx._frames = live_frames  # 会话与上下文共享同一份引用

            bars: dict[str, Bar] = {}
            indexes: dict[str, int] = {}
            for symbol in symbols:
                price = prices.get(symbol)
                if not price:
                    continue
                df = self._update_today_row(live_frames[symbol], price)
                live_frames[symbol] = df
                i = len(df) - 1
                bars[symbol] = bar_from_frame(df, i, symbol)
                indexes[symbol] = i

            if bars:
                session.frames = live_frames
                session.ctx._frames = live_frames
                session.step(now, bars, indexes)
                self.last_prices = {s: b.close for s, b in bars.items()}
                if self.recorder is not None:
                    self.recorder.touch()

            self._sleep(interval)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _append_today_row(frame: pd.DataFrame, today: date, price: float | None) -> pd.DataFrame:
        return append_today_row(frame, today, price)

    @staticmethod
    def _update_today_row(frame: pd.DataFrame, price: float) -> pd.DataFrame:
        return update_today_row(frame, price)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancelled or self._paused.is_set():
                return
            time.sleep(min(0.5, end - time.monotonic()))

    def _wait_if_paused(self) -> None:
        while self._paused.is_set() and not self.cancelled:
            time.sleep(0.1)

    # ------------------------------------------------------------------ #
    def _fill_result(self, result: EngineResult, session: TradingSession) -> None:
        from ..metrics.performance import compute

        curve = pd.DataFrame(session.equity_curve)
        if len(curve):
            equity = pd.Series(curve["total_equity"].to_numpy(), index=pd.to_datetime(curve["dt"]))
            result.metrics = compute(
                equity,
                trades=session.fills,
                initial_equity=self.config.initial_cash,
                rf_annual=self.config.risk_free_rate,
            )
        result.equity_curve = session.equity_curve
        result.trades = [t.to_dict() for t in session.fills]
        result.orders = [o.to_dict() for o in session.orders]
        result.positions = [p.to_dict() for p in session.account.positions.values() if not p.is_flat]
        result.logs = [
            {"ts": e.ts.isoformat(), **e.payload}
            for e in self._recorder_events.events
            if e.type is EventType.LOG
        ]

    def pause(self) -> bool:
        """暂停推进（不结束会话，账户/策略/进度全部保留）。"""
        if self.status is not EngineStatus.RUNNING:
            return False
        self._paused.set()
        self.bus.emit_status("paused", "纸盘已暂停", source="paper")
        return True

    def resume(self) -> bool:
        """从暂停处继续。"""
        if not self._paused.is_set():
            return False
        self._paused.clear()
        self.bus.emit_status("running", "纸盘已继续", source="paper")
        return True

    def stop(self) -> None:
        self._paused.clear()  # 唤醒等待中的主循环
        self.cancel()
        self.status = EngineStatus.STOPPING

    # ------------------------------------------------------------------ #
    def switch_strategy(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
        *,
        cancel_orders: bool = True,
    ) -> dict[str, Any]:
        """请求切换策略（下一轮 step 生效）。"""
        if self.session is None or self.status is not EngineStatus.RUNNING:
            raise RuntimeError("纸盘会话未在运行")
        strategy_cls = get_strategy(strategy_id)  # 未知策略抛 KeyError
        old_id = self.session.current_strategy_id
        self.session.request_switch(strategy_cls, params, cancel_orders=cancel_orders)
        return {
            "from": old_id,
            "to": strategy_id,
            "cancel_orders": cancel_orders,
            "pending": True,
        }

    # ------------------------------------------------------------------ #
    def state(self) -> dict[str, Any]:
        """给 API 的实时状态快照。"""
        account = self.account
        return {
            "run_id": self.config.run_id,
            "strategy_id": self.session.current_strategy_id if self.session else self.config.strategy_id,
            "symbols": self.config.symbols,
            "mode": self.options.mode,
            "status": "paused"
            if self._paused.is_set() and self.status is EngineStatus.RUNNING
            else self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_prices": self.last_prices,
            "cash": round(account.cash, 2) if account else 0.0,
            "frozen_cash": round(account.frozen_cash, 2) if account else 0.0,
            "equity": round(account.equity(), 2) if account else 0.0,
            "total_return": round(account.equity() / account.initial_cash - 1, 6)
            if account and account.initial_cash
            else 0.0,
            "positions": [p.to_dict() for p in account.holdings()] if account else [],
            "open_orders": [o.to_dict() for o in self.broker.open_orders] if self.broker else [],
            "recent_fills": [f.to_dict() for f in self.broker.fills[-50:]] if self.broker else [],
            "equity_curve": self.session.equity_curve[-500:] if self.session else [],
            "error": self.last_error,
            "steps": self.session.stats.steps if self.session else 0,
        }


class PaperRunner:
    """在后台线程里跑一个 :class:`PaperEngine`，供 Web 层启停与查询。"""

    def __init__(self) -> None:
        self._engine: PaperEngine | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, engine: PaperEngine) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("已有纸盘会话在运行，请先停止")
            self._engine = engine
            self._thread = threading.Thread(target=self._wrap, args=(engine,), name="paper-engine", daemon=True)
            self._thread.start()

    def _wrap(self, engine: PaperEngine) -> None:
        try:
            engine.run()
        except Exception:  # noqa: BLE001
            log.exception("纸盘线程异常退出")
        finally:
            with self._lock:
                self._thread = None

    def stop(self, timeout: float = 10.0) -> bool:
        with self._lock:
            engine = self._engine
            thread = self._thread
        if engine is None:
            return False
        engine.stop()
        if thread is not None:
            thread.join(timeout=timeout)
        return not (thread and thread.is_alive())

    @property
    def engine(self) -> PaperEngine | None:
        return self._engine

    def state(self) -> dict[str, Any] | None:
        engine = self._engine
        return engine.state() if engine else None


class PaperManager:
    """按用户隔离的纸盘会话池：**同一用户可同时运行多个会话**。

    每个会话有独立的虚拟账户与后台线程（虚拟资金互不影响），用 ``run_id`` 区分；
    事件在 WebSocket 层仍按 ``user_id`` 定向投递。同一用户并发会话数有上限
    （默认 5），防止误开一堆会话占满线程与行情请求。
    """

    def __init__(self, max_sessions_per_user: int = 5) -> None:
        self._runners: dict[int, dict[str, PaperRunner]] = {}
        self._lock = threading.RLock()
        self.max_sessions_per_user = max(1, int(max_sessions_per_user))

    # ------------------------------------------------------------------ #
    def runners(self, user_id: int) -> dict[str, PaperRunner]:
        with self._lock:
            return dict(self._runners.get(user_id, {}))

    def runner(self, user_id: int, run_id: str | None = None) -> PaperRunner | None:
        with self._lock:
            slots = self._runners.get(user_id, {})
            if run_id:
                return slots.get(run_id)
            running = [item for item in slots.values() if item.running]
            if running:
                return running[-1]
            return next(iter(slots.values()), None)

    def running(self, user_id: int, run_id: str | None = None) -> bool:
        if run_id:
            runner = self.runner(user_id, run_id)
            return bool(runner and runner.running)
        return any(item.running for item in self.runners(user_id).values())

    @property
    def active_users(self) -> list[int]:
        with self._lock:
            return [
                uid
                for uid, slots in self._runners.items()
                if any(item.running for item in slots.values())
            ]

    # ------------------------------------------------------------------ #
    def start(self, user_id: int, engine: PaperEngine) -> None:
        """启动一个会话；同 run_id 重复启动或超过并发上限时抛 ``RuntimeError``。"""
        run_id = engine.config.run_id
        with self._lock:
            slots = self._runners.setdefault(user_id, {})
            # 顺手清理已经结束的会话，避免字典无限增长
            for stale in [rid for rid, item in slots.items() if not item.running]:
                slots.pop(stale, None)
            if run_id in slots:
                raise RuntimeError("该运行已在运行")
            if len(slots) >= self.max_sessions_per_user:
                raise RuntimeError(
                    f"同时运行的模拟盘不能超过 {self.max_sessions_per_user} 个，请先停止一些"
                )
            runner = PaperRunner()
            slots[run_id] = runner
        runner.start(engine)

    def stop(self, user_id: int, run_id: str | None = None, timeout: float = 10.0) -> int:
        """停止指定会话；不传 ``run_id`` 时停止该用户全部会话。返回停止数量。"""
        with self._lock:
            slots = self._runners.get(user_id, {})
            targets = [
                (rid, item)
                for rid, item in slots.items()
                if item.running and (run_id is None or rid == run_id)
            ]
        stopped = 0
        for rid, runner in targets:
            try:
                if runner.stop(timeout=timeout):
                    stopped += 1
            except Exception:  # noqa: BLE001
                log.exception("停止用户 %s 的纸盘会话 %s 失败", user_id, rid)
        with self._lock:
            for rid, _ in targets:
                slots.pop(rid, None)
        return stopped

    def engine(self, user_id: int, run_id: str | None = None) -> PaperEngine | None:
        runner = self.runner(user_id, run_id)
        return runner.engine if runner is not None else None

    def state(self, user_id: int, run_id: str | None = None) -> dict[str, Any] | None:
        runner = self.runner(user_id, run_id)
        return runner.state() if runner is not None else None

    def pause(self, user_id: int, run_id: str | None = None) -> bool:
        engine = self.engine(user_id, run_id)
        return bool(engine and engine.pause())

    def resume(self, user_id: int, run_id: str | None = None) -> bool:
        engine = self.engine(user_id, run_id)
        return bool(engine and engine.resume())

    def sessions(self, user_id: int) -> list[dict[str, Any]]:
        """运行中的会话状态列表（多会话页面用）。"""
        out: list[dict[str, Any]] = []
        for runner in self.runners(user_id).values():
            if not runner.running:
                continue
            state = runner.state()
            if state:
                out.append(state)
        return out

    def stop_all(self, timeout: float = 10.0) -> None:
        """服务关闭时停掉所有用户的会话。"""
        with self._lock:
            users = list(self._runners)
        for user_id in users:
            try:
                self.stop(user_id, timeout=timeout)
            except Exception:  # noqa: BLE001
                log.exception("停止用户 %s 的纸盘会话失败", user_id)


__all__ = ["PaperEngine", "PaperManager", "PaperOptions", "PaperResumeState", "PaperRunner"]
