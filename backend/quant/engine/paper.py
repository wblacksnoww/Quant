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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from ..core.clock import CN_TZ, RealClock, SimClock
from ..core.events import EventBus, EventRecorder, EventType
from ..core.portfolio import Account
from ..core.types import Bar, EngineStatus
from ..data.calendar import is_market_open
from ..data.store import MarketDataService
from ..execution.broker import SimBroker
from ..execution.cost import CostModel
from ..execution.matching import MatchingEngine
from ..risk.base import RiskManager
from ..strategy.base import Strategy
from ..strategy.registry import get_strategy
from .base import BaseEngine, EngineConfig, EngineResult
from .session import TradingSession, bar_from_frame

log = logging.getLogger(__name__)


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
    ) -> None:
        super().__init__(config)
        self.data = data
        self.options = options or PaperOptions()
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
        )

    # ------------------------------------------------------------------ #
    # replay：加速回放历史
    # ------------------------------------------------------------------ #
    def _run_replay(self, frames: dict[str, pd.DataFrame], session: TradingSession) -> None:
        symbols = list(frames)
        index_maps = {s: {d: i for i, d in enumerate(frames[s]["date"].dt.date)} for s in symbols}
        dates = sorted({d for s in symbols for d in index_maps[s]})
        interval = max(0.0, self.options.replay_interval)

        for day in dates:
            if self.cancelled:
                return
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

            # 新交易日：给每个标的追加一根"今日"的活 bar
            if current_day != today:
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
        price = price or float(frame["close"].iloc[-1])
        row = {
            "date": pd.Timestamp(today),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0.0,
            "amount": 0.0,
            "prev_close": float(frame["close"].iloc[-1]),
            "limit_up": None,
            "limit_down": None,
        }
        if len(frame) and frame["date"].iloc[-1].date() == today:
            return frame
        out = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        return out

    @staticmethod
    def _update_today_row(frame: pd.DataFrame, price: float) -> pd.DataFrame:
        if len(frame) == 0:
            return frame
        idx = frame.index[-1]
        frame = frame.copy()
        frame.loc[idx, "close"] = price
        frame.loc[idx, "high"] = max(float(frame.loc[idx, "high"]), price)
        frame.loc[idx, "low"] = min(float(frame.loc[idx, "low"]), price)
        frame.loc[idx, "amount"] = price * float(frame.loc[idx, "volume"])
        return frame

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancelled:
                return
            time.sleep(min(0.5, end - time.monotonic()))

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

    def stop(self) -> None:
        self.cancel()
        self.status = EngineStatus.STOPPING

    # ------------------------------------------------------------------ #
    def state(self) -> dict[str, Any]:
        """给 API 的实时状态快照。"""
        account = self.account
        return {
            "run_id": self.config.run_id,
            "strategy_id": self.config.strategy_id,
            "symbols": self.config.symbols,
            "mode": self.options.mode,
            "status": self.status.value,
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
    """按用户隔离的纸盘会话池：每个用户一个 :class:`PaperRunner`。

    同一用户同一时间只允许一个会话；不同用户互不影响（各自线程、各自记录）。
    每个会话的事件在 WebSocket 层按 ``user_id`` 定向投递，不会广播给其他人。
    """

    def __init__(self) -> None:
        self._runners: dict[int, PaperRunner] = {}
        self._lock = threading.RLock()

    def runner(self, user_id: int) -> PaperRunner | None:
        with self._lock:
            return self._runners.get(user_id)

    def _slot(self, user_id: int) -> PaperRunner:
        with self._lock:
            runner = self._runners.get(user_id)
            if runner is None:
                runner = PaperRunner()
                self._runners[user_id] = runner
            return runner

    # ------------------------------------------------------------------ #
    def running(self, user_id: int) -> bool:
        runner = self.runner(user_id)
        return bool(runner and runner.running)

    @property
    def active_users(self) -> list[int]:
        with self._lock:
            return [uid for uid, runner in self._runners.items() if runner.running]

    def start(self, user_id: int, engine: PaperEngine) -> None:
        """``RuntimeError``：该用户已有会话在运行。"""
        self._slot(user_id).start(engine)

    def stop(self, user_id: int, timeout: float = 10.0) -> bool:
        runner = self.runner(user_id)
        return runner.stop(timeout=timeout) if runner is not None else False

    def engine(self, user_id: int) -> PaperEngine | None:
        runner = self.runner(user_id)
        return runner.engine if runner is not None else None

    def state(self, user_id: int) -> dict[str, Any] | None:
        runner = self.runner(user_id)
        return runner.state() if runner is not None else None

    def stop_all(self, timeout: float = 10.0) -> None:
        """服务关闭时停掉所有用户的会话。"""
        with self._lock:
            runners = list(self._runners.items())
        for user_id, runner in runners:
            try:
                runner.stop(timeout=timeout)
            except Exception:  # noqa: BLE001
                log.exception("停止用户 %s 的纸盘会话失败", user_id)


__all__ = ["PaperEngine", "PaperManager", "PaperOptions", "PaperRunner"]
