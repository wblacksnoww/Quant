"""实盘引擎：真实时钟 + 交易所回报 + 真实下单。

与纸盘 live 模式的差别只有一个：**订单真的发到交易所**。
策略、风控、账户记账、事件推送仍然复用同一套组件，所以"回测 → 纸盘 → 实盘"
三段行为可以对照着看。

安全设计（三重闸门，缺一不可）：

1. 服务端配置 ``QUANT_ENABLE_LIVE_TRADING=true``（默认关闭）；
2. 适配器默认 ``sandbox=True`` 走测试网；真实资金必须显式关闭测试网；
3. 路由层要求创建时二次确认（``confirm_live``），并写审计。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from ..core.clock import CN_TZ, RealClock
from ..core.events import EventBus, EventRecorder, EventType
from ..core.portfolio import Account
from ..core.types import Bar, EngineStatus
from ..data.calendar import is_market_open
from ..data.store import MarketDataService
from ..execution.adapters.base import AdapterError, BrokerAdapter
from ..execution.cost import CostModel
from ..execution.live_broker import LiveBroker
from ..risk.base import RiskManager
from ..strategy.base import Strategy
from ..strategy.registry import get_strategy
from .base import BaseEngine, EngineConfig, EngineResult
from .live_frames import append_today_row, update_today_row
from .session import TradingSession, bar_from_frame

log = logging.getLogger(__name__)


@dataclass
class LiveOptions:
    poll_interval: float = 5.0
    warmup_days: int = 120  # 启动时加载的历史 bar（指标预热）
    respect_market_hours: bool = True
    reconcile_interval: float = 300.0  # 对账间隔（秒）
    cancel_on_stop: bool = True


class LiveEngine(BaseEngine):
    """实盘交易引擎（阻塞运行，由 :class:`LiveRunner` 放到后台线程）。"""

    def __init__(
        self,
        config: EngineConfig,
        data: MarketDataService,
        adapter: BrokerAdapter,
        *,
        options: LiveOptions | None = None,
        bus: EventBus | None = None,
        recorder: Any = None,
    ) -> None:
        super().__init__(config)
        self.data = data
        self.adapter = adapter
        self.options = options or LiveOptions()
        self.bus = bus or EventBus(f"live:{config.run_id}")
        self.recorder = recorder
        self._recorder_events = EventRecorder(self.bus, limit=5000)
        self.status = EngineStatus.IDLE
        self.account: Account | None = None
        self.broker: LiveBroker | None = None
        self.session: TradingSession | None = None
        self.last_prices: dict[str, float] = {}
        self.last_error: str | None = None
        self.started_at: datetime | None = None
        self.reconcile_at: str | None = None
        self._last_reconcile = 0.0

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
                raise RuntimeError("实盘启动失败：没有可用的历史行情（指标预热失败）")

            self.adapter.connect()
            session = self._build_session(frames)
            self.session = session
            snapshot = self.broker.bind_account(self.config.symbols, self.last_prices) if self.broker else {}
            self.bus.emit_log(
                f"实盘账户已绑定（{'测试网' if self.adapter.sandbox else '真实资金'}）："
                f"现金 {snapshot.get('cash', 0):.2f} {snapshot.get('currency', '')}",
                level="warning" if not self.adapter.sandbox else "info",
                source="live",
            )
            session.begin()
            self.status = EngineStatus.RUNNING
            self.bus.emit_status("running", "实盘已启动", source="live")

            self._run_live(frames, session)

            session.finish()
            self.status = EngineStatus.FINISHED if not self.cancelled else EngineStatus.STOPPED
            result.status = self.status.value
        except Exception as exc:  # noqa: BLE001
            log.exception("实盘运行失败")
            self.status = EngineStatus.FAILED
            self.last_error = f"{type(exc).__name__}: {exc}"
            result.status = "failed"
            result.error = self.last_error
            self.bus.emit_status("failed", self.last_error, source="live")
        finally:
            try:
                self.adapter.close()
            except Exception:  # noqa: BLE001
                log.exception("关闭实盘连接失败")
            result.finished_at = datetime.now()
            result.elapsed_seconds = time.monotonic() - started
            if self.session is not None:
                self._fill_result(result, self.session)
            self._recorder_events.close()
            if self.recorder is not None:
                try:
                    self.recorder.finalize(result)
                except Exception:  # noqa: BLE001
                    log.exception("实盘结果落库失败")
        return result

    # ------------------------------------------------------------------ #
    def _config_dict(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "strategy_id": cfg.strategy_id,
            "symbols": cfg.symbols,
            "initial_cash": cfg.initial_cash,
            "params": cfg.params,
            "cost": cfg.cost,
            "risk": cfg.risk,
            "mode": "live",
            "adapter": self.adapter.name,
            "sandbox": self.adapter.sandbox,
        }

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        cfg = self.config
        frames: dict[str, pd.DataFrame] = {}
        for symbol in cfg.symbols:
            try:
                frame = self.data.bars(symbol, cfg.start, cfg.end)
            except Exception as exc:  # noqa: BLE001
                log.warning("实盘预热 %s 失败: %s", symbol, exc)
                continue
            if frame is not None and len(frame):
                frames[symbol] = frame.reset_index(drop=True)
                self.last_prices[symbol] = float(frame["close"].iloc[-1])
        return frames

    def _build_session(self, frames: dict[str, pd.DataFrame]) -> TradingSession:
        cfg = self.config
        cost = CostModel.for_market(cfg.market, cfg.cost)
        account = Account(cfg.initial_cash, name=cfg.run_id)
        broker = LiveBroker(
            account,
            self.adapter,
            self.bus,
            cost_model=cost,
            name=f"live:{cfg.run_id}",
        )
        risk = RiskManager.from_config(cfg.risk, bus=self.bus)
        strategy_cls: type[Strategy] = get_strategy(cfg.strategy_id)
        self.account = account
        self.broker = broker
        return TradingSession(
            strategy_cls=strategy_cls,
            strategy_params=cfg.params,
            account=account,
            broker=broker,
            risk=risk,
            clock=RealClock(),
            bus=self.bus,
            frames=frames,
            strategy_name=cfg.strategy_id,
            recorder=self.recorder,
            auto_end_of_day=False,  # 跨日才撤 DAY 单
        )

    # ------------------------------------------------------------------ #
    def _run_live(self, frames: dict[str, pd.DataFrame], session: TradingSession) -> None:
        symbols = list(frames)
        live_frames = {s: frames[s].copy() for s in symbols}
        current_day = None
        interval = max(1.0, self.options.poll_interval)
        market = self.config.market

        while not self.cancelled:
            now = datetime.now(CN_TZ)
            today = now.date()

            if self.options.respect_market_hours and not is_market_open(market, now):
                self.bus.emit_status("idle", "非交易时段，等待开盘", source="live")
                self._sleep(30)
                continue

            # 1) 先同步交易所回报（成交/撤单/拒单），再让策略看到最新账户
            assert self.broker is not None
            self.broker.sync()
            self._maybe_reconcile()

            # 2) 行情：优先适配器报价，其次行情数据源
            prices = self._prices(symbols)
            if not prices:
                self.bus.emit_log("实时行情不可用，等待下一轮", level="warning", source="live")
                self._sleep(interval)
                continue

            # 3) 跨日：昨日 DAY 单撤单，追加今日活 bar
            if current_day != today:
                if current_day is not None:
                    self.broker.end_of_day()
                current_day = today
                for symbol in symbols:
                    live_frames[symbol] = append_today_row(live_frames[symbol], today, prices.get(symbol))
                session.frames = live_frames
                session.ctx._frames = live_frames

            bars: dict[str, Bar] = {}
            indexes: dict[str, int] = {}
            for symbol in symbols:
                price = prices.get(symbol)
                if not price:
                    continue
                live_frames[symbol] = update_today_row(live_frames[symbol], price)
                i = len(live_frames[symbol]) - 1
                bars[symbol] = bar_from_frame(live_frames[symbol], i, symbol)
                indexes[symbol] = i

            if bars:
                session.frames = live_frames
                session.ctx._frames = live_frames
                session.step(now, bars, indexes)
                self.last_prices = {s: b.close for s, b in bars.items()}
                if self.recorder is not None:
                    self.recorder.touch()

            self._sleep(interval)

    def _prices(self, symbols: list[str]) -> dict[str, float]:
        try:
            prices = self.adapter.quote(symbols)
        except NotImplementedError:
            prices = {}
        except Exception as exc:  # noqa: BLE001
            log.warning("适配器报价失败，回退行情源: %s", exc)
            prices = {}
        if not prices:
            try:
                prices = self.data.realtime(symbols)
            except Exception as exc:  # noqa: BLE001
                log.warning("行情源实时价失败: %s", exc)
                prices = {}
        return {k: v for k, v in prices.items() if v and v > 0}

    def _maybe_reconcile(self) -> None:
        assert self.broker is not None
        now = time.monotonic()
        if now - self._last_reconcile < self.options.reconcile_interval:
            return
        self._last_reconcile = now
        try:
            report = self.broker.reconcile()
        except AdapterError as exc:
            self.bus.emit_log(f"对账失败: {exc}", level="warning", source="live")
            return
        self.reconcile_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
        if not report["ok"]:
            log.warning("实盘对账差异 %s", report)

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
        result.positions = [
            p.to_dict() for p in session.account.positions.values() if not p.is_flat
        ]
        result.logs = [
            {"ts": e.ts.isoformat(), **e.payload}
            for e in self._recorder_events.events
            if e.type is EventType.LOG
        ]

    def stop(self) -> None:
        self.cancel()
        self.status = EngineStatus.STOPPING

    def switch_strategy(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
        *,
        cancel_orders: bool = True,
    ) -> dict[str, Any]:
        """请求切换策略（下一轮 step 生效，默认撤销旧策略挂单）。"""
        if self.session is None or self.status is not EngineStatus.RUNNING:
            raise RuntimeError("实盘会话未在运行")
        strategy_cls = get_strategy(strategy_id)
        old_id = self.session.current_strategy_id
        self.session.request_switch(strategy_cls, params, cancel_orders=cancel_orders)
        return {
            "from": old_id,
            "to": strategy_id,
            "cancel_orders": cancel_orders,
            "pending": True,
        }

    def state(self) -> dict[str, Any]:
        account = self.account
        return {
            "run_id": self.config.run_id,
            "strategy_id": self.session.current_strategy_id if self.session else self.config.strategy_id,
            "symbols": self.config.symbols,
            "mode": "live",
            "adapter": self.adapter.name,
            "sandbox": self.adapter.sandbox,
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
            "reconcile_at": self.reconcile_at,
        }


class LiveRunner:
    """在后台线程里跑一个 :class:`LiveEngine`。"""

    def __init__(self) -> None:
        self._engine: LiveEngine | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, engine: LiveEngine) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("已有实盘会话在运行，请先停止")
            self._engine = engine
            self._thread = threading.Thread(
                target=self._wrap, args=(engine,), name="live-engine", daemon=True
            )
            self._thread.start()

    def _wrap(self, engine: LiveEngine) -> None:
        try:
            engine.run()
        except Exception:  # noqa: BLE001
            log.exception("实盘线程异常退出")
        finally:
            with self._lock:
                self._thread = None

    def stop(self, timeout: float = 15.0) -> bool:
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
    def engine(self) -> LiveEngine | None:
        return self._engine

    def state(self) -> dict[str, Any] | None:
        engine = self._engine
        return engine.state() if engine else None


class LiveManager:
    """按用户隔离的实盘会话池（与 PaperManager 同构）。

    实盘的隔离要求比纸盘更严格：同一账号不允许并发会话，否则会出现两套策略
    争抢同一笔资金。
    """

    def __init__(self) -> None:
        self._runners: dict[int, LiveRunner] = {}
        self._lock = threading.RLock()

    def runner(self, user_id: int) -> LiveRunner | None:
        with self._lock:
            return self._runners.get(user_id)

    def _slot(self, user_id: int) -> LiveRunner:
        with self._lock:
            runner = self._runners.get(user_id)
            if runner is None:
                runner = LiveRunner()
                self._runners[user_id] = runner
            return runner

    def running(self, user_id: int) -> bool:
        runner = self.runner(user_id)
        return bool(runner and runner.running)

    def start(self, user_id: int, engine: LiveEngine) -> None:
        self._slot(user_id).start(engine)

    def stop(self, user_id: int, timeout: float = 15.0) -> bool:
        runner = self.runner(user_id)
        return runner.stop(timeout=timeout) if runner is not None else False

    def engine(self, user_id: int) -> LiveEngine | None:
        runner = self.runner(user_id)
        return runner.engine if runner is not None else None

    def state(self, user_id: int) -> dict[str, Any] | None:
        runner = self.runner(user_id)
        return runner.state() if runner is not None else None

    @property
    def active_users(self) -> list[int]:
        with self._lock:
            return [uid for uid, runner in self._runners.items() if runner.running]

    def stop_all(self, timeout: float = 15.0) -> None:
        with self._lock:
            runners = list(self._runners.items())
        for user_id, runner in runners:
            try:
                runner.stop(timeout=timeout)
            except Exception:  # noqa: BLE001
                log.exception("停止用户 %s 的实盘会话失败", user_id)


__all__ = ["LiveEngine", "LiveManager", "LiveOptions", "LiveRunner"]
