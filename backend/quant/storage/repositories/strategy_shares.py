"""策略共享关系仓储。"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import StrategyShare, UserCustomStrategy


class StrategyShareRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, strategy_id: int, owner_id: int, target_user_id: int | None) -> StrategyShare:
        record = StrategyShare(
            strategy_id=strategy_id,
            owner_id=owner_id,
            target_user_id=target_user_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, share_id: int) -> StrategyShare | None:
        return self.session.get(StrategyShare, share_id)

    def find(self, strategy_id: int, target_user_id: int | None) -> StrategyShare | None:
        stmt = select(StrategyShare).where(StrategyShare.strategy_id == strategy_id)
        if target_user_id is None:
            stmt = stmt.where(StrategyShare.target_user_id.is_(None))
        else:
            stmt = stmt.where(StrategyShare.target_user_id == target_user_id)
        return self.session.scalar(stmt)

    def list_for_strategy(self, strategy_id: int) -> list[StrategyShare]:
        return list(
            self.session.scalars(
                select(StrategyShare)
                .where(StrategyShare.strategy_id == strategy_id)
                .order_by(StrategyShare.created_at)
            )
        )

    def visible_for(self, user_id: int) -> list[tuple[StrategyShare, UserCustomStrategy]]:
        stmt = (
            select(StrategyShare, UserCustomStrategy)
            .join(UserCustomStrategy, StrategyShare.strategy_id == UserCustomStrategy.id)
            .where(
                or_(
                    StrategyShare.target_user_id == user_id,
                    StrategyShare.target_user_id.is_(None),
                ),
                UserCustomStrategy.user_id != user_id,
            )
            .order_by(StrategyShare.created_at.desc())
        )
        return [(share, strategy) for share, strategy in self.session.execute(stmt).all()]

    def exists_for(self, strategy_id: int, user_id: int) -> bool:
        return self.find(strategy_id, user_id) is not None or self.find(strategy_id, None) is not None

    def delete(self, record: StrategyShare) -> None:
        self.session.delete(record)

    def count_for_strategy(self, strategy_id: int) -> int:
        return len(self.list_for_strategy(strategy_id))


__all__ = ["StrategyShareRepository"]
