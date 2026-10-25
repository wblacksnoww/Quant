"""参数寻优任务管理。

与回测不同，寻优**不落库**：它是一次探索性计算，产物是"哪个参数区间更稳"的
判断，而不是需要长期留存的运行记录。放内存换来的是零迁移成本和更快的状态读取；
代价是重启后历史寻优丢失 —— 这个取舍在寻优场景下是划算的。

同时只允许一个寻优任务：它是纯 CPU 密集的（几十上百次回测），并发跑会让
每个任务都变慢，还会和回测抢核。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.types import utcnow
from ..optimize import (
    OptimizeCancelled,
    Optimizer,
    OptimizeRequest,
    OptimizeResult,
)
from ..optimize.space import count_combinations

log = logging.getLogger(__name__)

__all__ = ["OptimizeJob", "OptimizeService"]


@dataclass
class OptimizeJob:
    job_id: str
    mode: str
    status: str = "queued"  # queued / running / finished / failed / cancelled
    progress: float = 0.0
    message: str = ""
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    error: str | None = None
    request: OptimizeRequest | None = None
    result: OptimizeResult | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds") if self.finished_at else None,
            "error": self.error,
        }

    def to_full(self, *, trial_limit: int = 100) -> dict[str, Any]:
        payload = self.to_summary()
        result = self.result
        if result is None:
            payload.update({"summary": {}, "best": None, "trials": [], "folds": [], "total_trials": 0})
            return payload

        # 只回传按目标排序的前 N 条：结论是"前几名能不能站住"，
        # 不是"第 800 名长什么样"。
        ranked = sorted(
            result.trials,
            key=lambda t: (t.status == "ok", t.score if t.score is not None else float("-inf")),
            reverse=True,
        )
        payload.update(
            {
                "summary": result.summary,
                "best": result.best.to_dict() if result.best else None,
                "trials": [t.to_dict() for t in ranked[:trial_limit]],
                "folds": [f.to_dict() for f in result.folds],
                "total_trials": len(result.trials),
            }
        )
        return payload


class OptimizeService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._jobs: dict[str, OptimizeJob] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    @property
    def active(self) -> str | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in {"queued", "running"}:
                    return job.job_id
        return None

    def submit(self, request: OptimizeRequest, *, mode: str) -> str:
        with self._lock:
            if self.active is not None:
                raise RuntimeError("已有寻优任务在运行，请等它结束或先取消")
            job = OptimizeJob(job_id=f"opt_{uuid.uuid4().hex[:12]}", mode=mode, request=request)
            self._jobs[job.job_id] = job
            # 只保留最近 20 条，避免长时间运行进程里任务表无限增长
            if len(self._jobs) > 20:
                for key in list(self._jobs)[:-20]:
                    if self._jobs[key].status not in {"queued", "running"}:
                        self._jobs.pop(key, None)
            self._thread = threading.Thread(
                target=self._run, args=(job,), name=f"optimize-{job.job_id}", daemon=True
            )
            self._thread.start()
        return job.job_id

    def get(self, job_id: str) -> OptimizeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 20) -> list[OptimizeJob]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"queued", "running"}:
                return False
            job.cancel.set()
            job.message = "正在取消…"
        return True

    # ------------------------------------------------------------------ #
    def _run(self, job: OptimizeJob) -> None:
        request = job.request
        assert request is not None  # submit 时已保证

        def progress(value: float, message: str) -> None:
            job.progress = max(0.0, min(1.0, value))
            job.message = message

        job.status = "running"
        job.message = "加载行情数据"
        try:

            def news_for(start, end):
                """按 trial 区间构造新闻源，避免样本外掺入未来新闻。"""
                from datetime import time as dt_time

                from ..news.provider import NewsFactorProvider

                return NewsFactorProvider(
                    self.ctx.db,
                    list(request.symbols),
                    start=datetime.combine(start, dt_time.min),
                    end=datetime.combine(end, dt_time.max),
                )

            optimizer = Optimizer(
                self.ctx.data,
                news_for=news_for,
                progress=progress,
                cancel=job.cancel.is_set,
            )
            if job.mode == "walk_forward":
                job.result = optimizer.walk_forward(request)
            else:
                job.result = optimizer.search(request)
            job.status = "finished"
            job.progress = 1.0
            job.message = "完成"
        except OptimizeCancelled:
            job.status = "cancelled"
            job.message = "已取消"
        except Exception as exc:  # noqa: BLE001 - 任务失败要落进状态，API 才能展示原因
            log.exception("寻优失败 %s", job.job_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "失败"
        finally:
            job.finished_at = utcnow()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)


def count_request_combinations(space: dict[str, Any]) -> int:
    """供路由预估组合数（不真正展开）。"""
    return count_combinations(space)
