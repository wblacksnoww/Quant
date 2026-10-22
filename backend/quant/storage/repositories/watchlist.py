"""账号级自选仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import WatchlistItem


class WatchlistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for(self, user_id: int) -> list[str]:
        rows = self.session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.sort_order, WatchlistItem.id)
        )
        return [row.symbol for row in rows]

    def replace(self, user_id: int, symbols: Sequence[str]) -> list[str]:
        self.session.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user_id))
        for index, symbol in enumerate(symbols):
            self.session.add(WatchlistItem(user_id=user_id, symbol=symbol, sort_order=index))
        self.session.flush()
        return list(symbols)


__all__ = ["WatchlistRepository"]
