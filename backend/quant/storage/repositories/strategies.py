"""用户自建策略仓储：版本快照、共享、fork 关系。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import StrategyVersion, UserCustomStrategy, utcnow_naive


class UserStrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    def _snapshot(self, record: UserCustomStrategy) -> None:
        existing = self.session.scalar(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == record.id,
                StrategyVersion.version == record.version,
            )
        )
        if existing is not None:
            return
        self.session.add(
            StrategyVersion(
                strategy_id=record.id,
                version=record.version,
                name=record.name,
                description=record.description,
                category=record.category,
                spec=record.spec,
                created_by=record.user_id,
            )
        )

    def ensure_version(self, record: UserCustomStrategy) -> None:
        """旧数据没有版本快照时补一份当前版本。"""
        self._snapshot(record)

    def create(
        self,
        owner_id: int,
        *,
        name: str,
        spec: dict[str, Any],
        description: str = "",
        category: str = "trend",
        source_strategy_id: int | None = None,
        source_version: int | None = None,
    ) -> UserCustomStrategy:
        record = UserCustomStrategy(
            user_id=owner_id,
            name=name,
            description=description,
            category=category,
            spec=spec,
            version=1,
            source_strategy_id=source_strategy_id,
            source_version=source_version,
            forked_at=utcnow_naive() if source_strategy_id is not None else None,
        )
        self.session.add(record)
        self.session.flush()
        self._snapshot(record)
        return record

    def update(
        self,
        record: UserCustomStrategy,
        *,
        name: str | None = None,
        spec: dict[str, Any] | None = None,
        description: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
        source_version: int | None = None,
    ) -> UserCustomStrategy:
        self.ensure_version(record)
        changed = False
        if name is not None and name != record.name:
            record.name = name
            changed = True
        if spec is not None and spec != record.spec:
            record.spec = spec
            changed = True
        if description is not None and description != record.description:
            record.description = description
            changed = True
        if category is not None and category != record.category:
            record.category = category
            changed = True
        if enabled is not None:
            record.enabled = enabled
        if source_version is not None:
            record.source_version = source_version
        if changed:
            record.version += 1
        record.updated_at = utcnow_naive()
        self.session.flush()
        if changed:
            self._snapshot(record)
        return record

    def get(self, strategy_id: int, *, owner_id: int | None = None) -> UserCustomStrategy | None:
        record = self.session.get(UserCustomStrategy, strategy_id)
        if record is None or (owner_id is not None and record.user_id != owner_id):
            return None
        self.ensure_version(record)
        return record

    def list_for(self, owner_id: int) -> list[UserCustomStrategy]:
        rows = list(
            self.session.scalars(
                select(UserCustomStrategy)
                .where(UserCustomStrategy.user_id == owner_id)
                .order_by(desc(UserCustomStrategy.updated_at))
            )
        )
        for record in rows:
            self.ensure_version(record)
        return rows

    def list_all(self) -> list[UserCustomStrategy]:
        rows = list(
            self.session.scalars(select(UserCustomStrategy).order_by(desc(UserCustomStrategy.updated_at)))
        )
        for record in rows:
            self.ensure_version(record)
        return rows

    def find_fork(self, owner_id: int, source_id: int) -> UserCustomStrategy | None:
        return self.session.scalar(
            select(UserCustomStrategy).where(
                UserCustomStrategy.user_id == owner_id,
                UserCustomStrategy.source_strategy_id == source_id,
            )
        )

    def forks_for_source(self, source_id: int) -> list[UserCustomStrategy]:
        return list(
            self.session.scalars(
                select(UserCustomStrategy).where(UserCustomStrategy.source_strategy_id == source_id)
            )
        )

    def delete(self, record: UserCustomStrategy) -> None:
        self.session.delete(record)


__all__ = ["UserStrategyRepository"]
