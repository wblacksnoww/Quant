"""新闻/政策应用服务：录入、查询、因子聚合。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from ..core.errors import ConflictError, NotFoundError, PermissionDenied
from ..core.markets import try_parse
from ..news.factors import compute_factor
from ..storage.models import utcnow_naive
from ..storage.repositories.news import NewsRepository


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value

    return value.astimezone(UTC).replace(tzinfo=None)


def _normalize_symbols(symbols: list[str]) -> list[str]:
    out: list[str] = []
    for raw in symbols:
        text = str(raw).strip()
        if not text:
            continue
        parsed = try_parse(text)
        value = parsed.id if parsed is not None else text.upper()
        if value not in out:
            out.append(value)
    return out


def ingest(ctx: Any, *, user_id: int, payload: Any) -> dict[str, Any]:
    symbols = _normalize_symbols(list(payload.symbols or []))
    market = str(payload.market or "").strip().upper()
    if not market and symbols:
        inst = try_parse(symbols[0])
        market = inst.market.value if inst is not None else ""
    published = _naive_utc(payload.published_at) if payload.published_at else utcnow_naive()
    effective = _naive_utc(payload.effective_at) if payload.effective_at else published
    try:
        with ctx.db.session() as session:
            record = NewsRepository(session).create(
                title=payload.title,
                summary=payload.summary,
                url=payload.url,
                source="manual",
                published_at=published,
                effective_at=effective,
                symbols=symbols,
                market=market,
                event_type=payload.event_type,
                sentiment=payload.sentiment,
                policy_risk=payload.policy_risk,
                confidence=payload.confidence,
                created_by=user_id,
            )
            return record.to_dict()
    except IntegrityError as exc:
        raise ConflictError("相同标题与发布时间的新闻已存在") from exc


def list_items(ctx: Any, *, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with ctx.db.session() as session:
        return [record.to_dict() for record in NewsRepository(session).list(symbol=symbol, limit=limit)]


def factor(
    ctx: Any,
    *,
    symbol: str,
    window: str = "1h",
    as_of: datetime | None = None,
    half_life_hours: float = 24.0,
) -> dict[str, Any]:
    as_of_naive = _naive_utc(as_of) if as_of else utcnow_naive()
    start = as_of_naive - timedelta(days=30)
    with ctx.db.session() as session:
        rows = NewsRepository(session).for_symbols(
            [symbol],
            start=start,
            end=as_of_naive,
            limit=5000,
        )
        items = [row.to_dict() for row in rows]
    return compute_factor(
        items,
        symbol=symbol,
        as_of=as_of_naive,
        window=window,
        half_life_hours=half_life_hours,
    ).to_dict()


def delete(ctx: Any, *, user_id: int, news_id: int, is_admin: bool = False) -> None:
    with ctx.db.session() as session:
        repo = NewsRepository(session)
        record = repo.get(news_id)
        if record is None:
            raise NotFoundError("新闻不存在")
        if not is_admin and record.created_by != user_id:
            raise PermissionDenied("无权删除该新闻")
        repo.delete(record)


__all__ = ["delete", "factor", "ingest", "list_items"]
