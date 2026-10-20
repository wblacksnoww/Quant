"""运行记录仓储（回测 / 纸盘 / 实盘共用 runs 表）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from ..models import Run


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        run_id: str,
        *,
        user_id: int | None,
        kind: str,
        strategy_id: str,
        symbols: Sequence[str],
        config: dict[str, Any],
        status: str = "running",
    ) -> Run:
        record = Run(
            id=run_id,
            user_id=user_id,
            kind=kind,
            strategy_id=strategy_id,
            symbols=list(symbols),
            config=config,
            status=status,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, run_id: str) -> Run | None:
        return self.session.get(Run, run_id)

    def list(
        self,
        *,
        user_id: int | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        stmt = select(Run).order_by(desc(Run.created_at)).limit(limit).offset(offset)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        if kind:
            stmt = stmt.where(Run.kind == kind)
        return list(self.session.scalars(stmt))

    def count(self, *, user_id: int | None = None) -> int:
        stmt = select(func.count()).select_from(Run)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        return int(self.session.scalar(stmt) or 0)

    def delete(self, run_id: str) -> bool:
        record = self.get(run_id)
        if record is None:
            return False
        self.session.delete(record)
        return True

    def mark_interrupted(self, *, exclude_run_ids: set[str] | None = None) -> int:
        """服务重启时，把残留的 running 标记为 interrupted（活跃租约除外）。"""
        stmt = update(Run).where(Run.status == "running")
        if exclude_run_ids:
            stmt = stmt.where(~Run.id.in_(exclude_run_ids))
        result = self.session.execute(stmt.values(status="interrupted", error="服务重启导致运行中断"))
        return int(result.rowcount or 0)
