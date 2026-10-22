"""新闻/政策原始条目仓储。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import NewsItem, utcnow_naive


class NewsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def raw_hash(*, title: str, published_at: datetime, source: str = "manual") -> str:
        raw = f"{title.strip()}|{published_at.isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create(
        self,
        *,
        title: str,
        summary: str = "",
        url: str = "",
        source: str = "manual",
        external_id: str | None = None,
        published_at: datetime,
        ingested_at: datetime | None = None,
        effective_at: datetime | None = None,
        symbols: Sequence[str] = (),
        market: str = "",
        event_type: str = "other",
        sentiment: float = 0.0,
        policy_risk: float = 0.0,
        confidence: float = 0.5,
        created_by: int | None = None,
    ) -> NewsItem:
        now = utcnow_naive()
        ingested = ingested_at or now
        effective = effective_at or max(published_at, ingested)
        record = NewsItem(
            source=source,
            external_id=external_id,
            title=title.strip(),
            summary=summary,
            url=url,
            published_at=published_at,
            ingested_at=ingested,
            effective_at=effective,
            symbols=list(symbols),
            market=market.strip().upper(),
            event_type=event_type,
            sentiment=max(-1.0, min(1.0, float(sentiment))),
            policy_risk=max(0.0, min(1.0, float(policy_risk))),
            confidence=max(0.0, min(1.0, float(confidence))),
            raw_hash=self.raw_hash(title=title, published_at=published_at, source=source),
            created_by=created_by,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, news_id: int) -> NewsItem | None:
        return self.session.get(NewsItem, news_id)

    def delete(self, record: NewsItem) -> None:
        self.session.delete(record)

    def list(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NewsItem]:
        stmt = select(NewsItem).order_by(desc(NewsItem.effective_at)).limit(limit).offset(offset)
        rows = list(self.session.scalars(stmt))
        if symbol:
            rows = [row for row in rows if _matches(row, symbol)]
        return rows

    def for_symbols(
        self,
        symbols: Sequence[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 5000,
    ) -> list[NewsItem]:
        stmt = select(NewsItem).order_by(NewsItem.effective_at.asc()).limit(limit)
        if start is not None:
            stmt = stmt.where(NewsItem.effective_at >= start)
        if end is not None:
            stmt = stmt.where(NewsItem.effective_at <= end)
        rows = list(self.session.scalars(stmt))
        wanted = set(symbols)
        return [row for row in rows if _matches_any(row, wanted)]


def _item_symbols(row: NewsItem) -> set[str]:
    return {str(item).upper() for item in (row.symbols or [])}


def _matches(row: NewsItem, symbol: str) -> bool:
    target = symbol.strip().upper()
    symbols = _item_symbols(row)
    market = target.split(":", 1)[0] if ":" in target else ""
    return target in symbols or "ALL" in symbols or (bool(row.market) and row.market == market)


def _matches_any(row: NewsItem, symbols: set[str]) -> bool:
    item_symbols = _item_symbols(row)
    if item_symbols & symbols or "ALL" in item_symbols:
        return True
    markets = {item.split(":", 1)[0] for item in symbols if ":" in item}
    return bool(row.market) and row.market in markets


__all__ = ["NewsRepository"]
