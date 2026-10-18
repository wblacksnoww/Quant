"""回测任务管理：线程池 + 运行记录 + WebSocket 广播。

刻意限制并发（默认 2）：回测是 CPU/IO 混合型任务，无限并发只会让每个任务都变慢，
还会拖垮同一台机器上的纸盘与 API。超出的请求返回 429，让用户排队而不是雪崩。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..core.events import EventBus
from ..engine import BacktestEngine, EngineConfig
from ..engine.base import EngineResult
from ..storage.repositories import RunRecorder
from .context import AppContext
from .ws import bus_bridge

log = logging.getLogger(__name__)


class BacktestManager:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._engines: dict[str, BacktestEngine] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    @property
    def running(self) -> list[str]:
        with self._lock:
            return [rid for rid, t in self._threads.items() if t.is_alive()]

    @property
    def running_count(self) -> int:
        return len(self.running)

    def can_accept(self) -> bool:
        return self.running_count < self.ctx.settings.max_concurrent_backtests

    # ------------------------------------------------------------------ #
    def submit(self, config: EngineConfig) -> str:
        recorder = RunRecorder(
            self.ctx.db,
            config.run_id,
            user_id=config.user_id,
            kind="backtest",
            strategy_id=config.strategy_id,
            symbols=config.symbols,
            config=self._config_payload(config),
        )
        recorder.start()

        bus = EventBus(f"backtest:{config.run_id}")
        engine = BacktestEngine(config, self.ctx.data, recorder=recorder, bus=bus)

        # 事件 → WebSocket（带 run_id + 用户归属，前端按会话过滤，其他人看不到）
        def forward(event) -> None:
            self.ctx.hub.publish(
                f"run.{event.type.value}",
                {**event.payload, "run_id": config.run_id},
                event.source or config.run_id,
                user_id=config.user_id,
            )

        unwire = []
        from ..core.events import EventType

        for event_type in EventType:
            unwire.append(bus.subscribe(event_type, forward))

        engine.on_progress(lambda p, m: self._on_progress(config.run_id, config.user_id, recorder, p, m))

        def runner() -> None:
            try:
                result = engine.run()
                self.ctx.hub.publish(
                    "run.finished",
                    {
                        "run_id": config.run_id,
                        "status": result.status,
                        "metrics": result.metrics.to_dict(),
                        "error": result.error,
                    },
                    config.run_id,
                    user_id=config.user_id,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("回测线程异常 run_id=%s", config.run_id)
                self.ctx.hub.publish(
                    "run.failed",
                    {"run_id": config.run_id, "error": str(exc)},
                    config.run_id,
                    user_id=config.user_id,
                )
            finally:
                for cancel in unwire:
                    cancel()
                with self._lock:
                    self._threads.pop(config.run_id, None)
                    self._engines.pop(config.run_id, None)

        thread = threading.Thread(target=runner, name=f"backtest-{config.run_id}", daemon=True)
        with self._lock:
            self._engines[config.run_id] = engine
            self._threads[config.run_id] = thread
        thread.start()
        log.info("回测已启动 run_id=%s strategy=%s symbols=%s", config.run_id, config.strategy_id, config.symbols)
        return config.run_id

    # ------------------------------------------------------------------ #
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            engine = self._engines.get(run_id)
        if engine is None:
            return False
        engine.cancel()
        return True

    def _on_progress(
        self,
        run_id: str,
        user_id: int | None,
        recorder: RunRecorder,
        progress: float,
        message: str,
    ) -> None:
        recorder.update_progress(progress, message)
        self.ctx.hub.publish(
            "run.progress",
            {"run_id": run_id, "progress": round(progress, 4), "message": message},
            run_id,
            user_id=user_id,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _config_payload(config: EngineConfig) -> dict[str, Any]:
        return {
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "initial_cash": config.initial_cash,
            "params": config.params,
            "cost": config.cost,
            "risk": config.risk,
            "benchmark": config.benchmark,
            "adjust": config.adjust,
        }

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            engines = list(self._engines.values())
            threads = list(self._threads.values())
        for engine in engines:
            engine.cancel()
        for thread in threads:
            thread.join(timeout=timeout)


__all__ = ["BacktestManager"]
