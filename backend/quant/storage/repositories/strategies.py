"""用户自建策略仓储（仅本人可见）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import UserCustomStrategy, utcnow_naive


class UserStrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        owner_id: int,
        *,
        name: str,
        spec: dict[str, Any],
        description: str = "",
        category: str = "trend",
    ) -> UserCustomStrategy:
        record = UserCustomStrategy(
            user_id=owner_id,
            name=name,
            description=description,
            category=category,
            spec=spec,
        )
        self.session.add(record)
        self.session.flush()
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
    ) -> UserCustomStrategy:
        if name is not None:
            record.name = name
        if spec is not None:
            record.spec = spec
        if description is not None:
            record.description = description
        if category is not None:
            record.category = category
        if enabled is not None:
            record.enabled = enabled
        record.updated_at = utcnow_naive()
        self.session.flush()
        return record

    def get(self, strategy_id: int, *, owner_id: int) -> UserCustomStrategy | None:
        """仅限本人可见——拼装策略不做跨用户共享。"""
        record = self.session.get(UserCustomStrategy, strategy_id)
        if record is None or record.user_id != owner_id:
            return None
        return record

    def list_for(self, owner_id: int) -> list[UserCustomStrategy]:
        return list(
            self.session.scalars(
                select(UserCustomStrategy)
                .where(UserCustomStrategy.user_id == owner_id)
                .order_by(desc(UserCustomStrategy.updated_at))
            )
        )

    def delete(self, record: UserCustomStrategy) -> None:
        self.session.delete(record)
