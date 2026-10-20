"""持久化回测队列仓储。

提交先把任务写入 ``backtest_jobs``，由任意实例的 worker 原子认领执行。
进程重启或多副本部署时，queued 任务不会丢；running 任务在心跳超时后重新入队。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from .db import Database
from .models import BacktestJob, utcnow_naive


class BacktestJobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    def create(self, run_id: str, *, user_id: int | None, config: dict[str, Any]) -> None:
        with self.db.session() as session:
            session.merge(
                BacktestJob(
                    run_id=run_id,
                    user_id=user_id,
                    config=config,
                    status="queued",
                    attempts=0,
                )
            )

    def claim(self, owner: str) -> dict[str, Any] | None:
        """原子认领最老的一条 queued 任务；抢不到返回 None。"""
        now = utcnow_naive()
        with self.db.session() as session:
            row = session.scalar(
                select(BacktestJob)
                .where(BacktestJob.status == "queued")
                .order_by(BacktestJob.created_at)
                .limit(1)
            )
            if row is None:
                return None
            result = session.execute(
                update(BacktestJob)
                .where(BacktestJob.run_id == row.run_id, BacktestJob.status == "queued")
                .values(
                    status="running",
                    owner=owner,
                    attempts=BacktestJob.attempts + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                return None
            return {
                "run_id": row.run_id,
                "user_id": row.user_id,
                "config": dict(row.config or {}),
                "attempts": int(row.attempts or 0) + 1,
            }

    def heartbeat(self, run_id: str, owner: str) -> None:
        try:
            with self.db.session() as session:
                session.execute(
                    update(BacktestJob)
                    .where(BacktestJob.run_id == run_id, BacktestJob.owner == owner)
                    .values(updated_at=utcnow_naive())
                )
        except Exception:  # noqa: BLE001 - 心跳失败不中断回测
            pass

    def finish(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.db.session() as session:
            session.execute(
                update(BacktestJob)
                .where(BacktestJob.run_id == run_id)
                .values(status=status, error=error, updated_at=utcnow_naive())
            )

    def cancel(self, run_id: str) -> bool:
        with self.db.session() as session:
            result = session.execute(
                update(BacktestJob)
                .where(
                    BacktestJob.run_id == run_id,
                    BacktestJob.status.in_(("queued", "running")),
                )
                .values(status="cancelled", updated_at=utcnow_naive())
            )
            return int(result.rowcount or 0) > 0

    def status(self, run_id: str) -> str | None:
        with self.db.session() as session:
            return session.scalar(select(BacktestJob.status).where(BacktestJob.run_id == run_id))

    def requeue_owned(self, owner: str) -> int:
        with self.db.session() as session:
            result = session.execute(
                update(BacktestJob)
                .where(BacktestJob.owner == owner, BacktestJob.status == "running")
                .values(status="queued", owner=None, updated_at=utcnow_naive())
            )
            return int(result.rowcount or 0)

    def requeue_stale(self, *, stale_seconds: float = 120.0) -> int:
        cutoff = utcnow_naive() - timedelta(seconds=stale_seconds)
        with self.db.session() as session:
            result = session.execute(
                update(BacktestJob)
                .where(BacktestJob.status == "running", BacktestJob.updated_at < cutoff)
                .values(status="queued", owner=None, updated_at=utcnow_naive())
            )
            return int(result.rowcount or 0)

    def active_count(self) -> int:
        with self.db.session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(BacktestJob)
                    .where(BacktestJob.status.in_(("queued", "running")))
                )
                or 0
            )

    def counts(self) -> dict[str, int]:
        with self.db.session() as session:
            rows = session.execute(
                select(BacktestJob.status, func.count()).group_by(BacktestJob.status)
            ).all()
            return {str(status): int(count) for status, count in rows}


__all__ = ["BacktestJobRepository"]
