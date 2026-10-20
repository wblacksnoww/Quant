"""场外基金申赎流水仓储。持仓由纯函数聚合，不落库。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FundTrade


class FundTradeRepository:
    """申赎流水仓。持仓不落库，由 :func:`quant.funds.compute_positions` 聚合。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **fields: Any) -> FundTrade:
        record = FundTrade(**fields)
        self.session.add(record)
        self.session.flush()
        return record

    def list_for(
        self, user_id: int, *, symbol: str | None = None, limit: int | None = 200
    ) -> list[FundTrade]:
        stmt = select(FundTrade).where(FundTrade.user_id == user_id)
        if symbol:
            stmt = stmt.where(FundTrade.symbol == symbol)
        stmt = stmt.order_by(FundTrade.trade_date.asc(), FundTrade.id.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def get(self, trade_id: int, *, user_id: int | None = None) -> FundTrade | None:
        record = self.session.get(FundTrade, trade_id)
        if record is None:
            return None
        if user_id is not None and record.user_id != user_id:
            return None
        return record

    def delete(self, record: FundTrade) -> None:
        self.session.delete(record)
        self.session.flush()
