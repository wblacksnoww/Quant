"""回测任务管理：数据库持久化队列 + 本实例 worker。

提交只写队列（``queued``），worker 原子认领后执行；重启时 queued 任务保留，
running 任务在心跳超时后重新入队。多实例部署时各实例 worker 通过条件更新
竞争同一条任务，不会重复执行。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any

from ..core.events import EventBus
from ..engine import BacktestEngine
from ..engine.base import EngineConfig, EngineResult
from ..services.event_bridge import bus_bridge
from ..storage.backtest_jobs import BacktestJobRepository
from ..storage.models import utcnow_naive
from ..storage.recorder import RunRecorder
from ..storage.repositories.runs import RunRepository
from .context import AppContext

log = logging.getLogger(__name__)


class BacktestManager:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.instance_id = ctx.instance_id
        self.jobs = BacktestJobRepository(ctx.db)
        self._engines: dict[str, BacktestEngine] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._last_check: dict[str, float] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()

        # 上次进程异常退出：心跳过期的 running 任务重新排队
        try:
            requeued = self.jobs.requeue_stale(stale_seconds=120)
            if requeued:
                log.warning("已将 %d 个失联的回测任务重新入队", requeued)
        except Exception:  # noqa: BLE001
            log.exception("回测队列恢复失败")

        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="backtest-dispatcher", daemon=True
        )
        self._dispatcher.start()

    # ------------------------------------------------------------------ #
    @property
    def running(self) -> list[str]:
        with self._lock:
            return [rid for rid, t in self._threads.items() if t.is_alive()]

    @property
    def running_count(self) -> int:
        return len(self.running)

    def can_accept(self) -> bool:
        try:
            return self.jobs.active_count() < self.ctx.settings.max_queued_backtests
        except Exception:  # noqa: BLE001
            return True

    # ------------------------------------------------------------------ #
    def submit(self, config: EngineConfig) -> str:
        """落库排队并唤醒 worker；返回 run_id（状态 queued）。"""
        payload = self._config_payload(config)
        with self.ctx.db.session() as session:
            RunRepository(session).create(
                config.run_id,
                user_id=config.user_id,
                kind="backtest",
                strategy_id=config.strategy_id,
                symbols=config.symbols,
                config=payload,
                status="queued",
            )
        self.jobs.create(config.run_id, user_id=config.user_id, config=payload)
        self._wake.set()
        log.info("回测已入队 run_id=%s strategy=%s", config.run_id, config.strategy_id)
        return config.run_id

    # ------------------------------------------------------------------ #
    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            while not self._stop.is_set() and self.running_count < self.ctx.settings.max_concurrent_backtests:
                try:
                    job = self.jobs.claim(self.instance_id)
                except Exception:  # noqa: BLE001
                    log.exception("认领回测任务失败")
                    break
                if job is None:
                    break
                try:
                    self._launch(job)
                except Exception as exc:  # noqa: BLE001
                    log.exception("启动回测任务失败 run_id=%s", job.get("run_id"))
                    self.jobs.finish(job["run_id"], "failed", str(exc))
                    self._mark_run_failed(job["run_id"], str(exc))

    def _launch(self, job: dict[str, Any]) -> None:
        config = self._config_from_payload(job["config"], run_id=job["run_id"], user_id=job["user_id"])
        recorder = RunRecorder(
            self.ctx.db,
            config.run_id,
            user_id=config.user_id,
            kind="backtest",
            strategy_id=config.strategy_id,
            symbols=config.symbols,
            config=job["config"],
        )
        recorder.start()

        bus = EventBus(f"backtest:{config.run_id}")
        engine = BacktestEngine(config, self.ctx.data, recorder=recorder, bus=bus)
        unwire = bus_bridge(
            bus, self.ctx.hub, source=config.run_id, run_id=config.run_id, user_id=config.user_id
        )
        engine.on_progress(
            lambda p, m: self._on_progress(config.run_id, config.user_id, recorder, engine, p, m)
        )

        def runner() -> None:
            result: EngineResult | None = None
            try:
                result = engine.run()
                status = result.status or "finished"
                self.jobs.finish(config.run_id, "done" if status == "finished" else status, result.error)
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
                self.jobs.finish(config.run_id, "failed", str(exc))
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
                    self._last_check.pop(config.run_id, None)
                self._wake.set()

        thread = threading.Thread(target=runner, name=f"backtest-{config.run_id}", daemon=True)
        with self._lock:
            self._engines[config.run_id] = engine
            self._threads[config.run_id] = thread
        thread.start()

    # ------------------------------------------------------------------ #
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            engine = self._engines.get(run_id)
        if engine is not None:
            engine.cancel()
            return True
        cancelled = self.jobs.cancel(run_id)
        if cancelled:
            self._mark_run_cancelled(run_id)
        return cancelled

    def _on_progress(
        self,
        run_id: str,
        user_id: int | None,
        recorder: RunRecorder,
        engine: BacktestEngine,
        progress: float,
        message: str,
    ) -> None:
        recorder.update_progress(progress, message)
        now = time.monotonic()
        if now - self._last_check.get(run_id, 0.0) >= 2.0:
            self._last_check[run_id] = now
            self.jobs.heartbeat(run_id, self.instance_id)
            if self.jobs.status(run_id) == "cancelled":
                engine.cancel()
        self.ctx.hub.publish(
            "run.progress",
            {"run_id": run_id, "progress": round(progress, 4), "message": message},
            run_id,
            user_id=user_id,
        )

    # ------------------------------------------------------------------ #
    def _mark_run_cancelled(self, run_id: str) -> None:
        try:
            with self.ctx.db.session() as session:
                record = RunRepository(session).get(run_id)
                if record is not None and record.status in ("queued", "running"):
                    record.status = "cancelled"
                    record.error = "已取消"
                    record.progress = 1.0
                    record.finished_at = utcnow_naive()
        except Exception:  # noqa: BLE001
            log.debug("标记回测取消失败 run_id=%s", run_id, exc_info=True)

    def _mark_run_failed(self, run_id: str, error: str) -> None:
        try:
            with self.ctx.db.session() as session:
                record = RunRepository(session).get(run_id)
                if record is not None:
                    record.status = "failed"
                    record.error = error
                    record.progress = 1.0
                    record.finished_at = utcnow_naive()
        except Exception:  # noqa: BLE001
            log.debug("标记回测失败状态失败 run_id=%s", run_id, exc_info=True)

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
            "volume_limit_pct": config.volume_limit_pct,
            "risk_free_rate": config.risk_free_rate,
        }

    @staticmethod
    def _config_from_payload(payload: dict[str, Any], *, run_id: str, user_id: int | None) -> EngineConfig:
        return EngineConfig(
            strategy_id=str(payload["strategy_id"]),
            symbols=list(payload["symbols"]),
            start=date.fromisoformat(str(payload["start"])),
            end=date.fromisoformat(str(payload["end"])),
            initial_cash=float(payload.get("initial_cash") or 100_000),
            params=dict(payload.get("params") or {}),
            cost=dict(payload.get("cost") or {}),
            risk=payload.get("risk"),
            benchmark=payload.get("benchmark"),
            adjust=str(payload.get("adjust") or "qfq"),
            volume_limit_pct=float(payload.get("volume_limit_pct", 0.10)),
            risk_free_rate=float(payload.get("risk_free_rate") or 0.0),
            run_id=run_id,
            user_id=user_id,
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            engines = list(self._engines.values())
            threads = list(self._threads.values())
        for engine in engines:
            engine.cancel()
        for thread in threads:
            thread.join(timeout=timeout)
        # 本实例没跑完的任务重新入队，下次启动/其他实例可继续
        try:
            self.jobs.requeue_owned(self.instance_id)
        except Exception:  # noqa: BLE001
            log.exception("回测任务重新入队失败")
        self._dispatcher.join(timeout=timeout)


__all__ = ["BacktestManager"]
