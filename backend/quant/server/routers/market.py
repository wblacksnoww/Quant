"""行情路由：K 线、实时快照、标的检索、交易日历、缓存管理。

所有接口统一使用规范标的 id（``MARKET:CODE``，如 ``CN:600519`` / ``US:AAPL`` /
``HK:00700``）；兼容输入（裸 6 位代码、``sh600519``、``aapl`` 等）在入口处
由 :func:`quant.core.markets.parse_instrument` 归一化，内部一律只认规范 id。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.errors import DataError, DataNotFound
from ...core.markets import Market, currency_symbol, parse_instrument
from ...data.base import frame_to_bars
from ...data.calendar import trading_days
from ...storage.models import User
from ..deps import get_current_user, get_ctx
from ..schemas import (
    BarsResponse,
    DataCacheOut,
    MarketOut,
    QuoteOut,
    QuoteResponse,
    SymbolOut,
)
from ..context import AppContext

router = APIRouter(prefix="/market", tags=["行情"])
log = logging.getLogger(__name__)


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式应为 YYYY-MM-DD: {value}") from exc


def _instrument(symbol: str):
    try:
        return parse_instrument(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/markets", response_model=list[MarketOut], summary="支持的市场")
def markets(_: User = Depends(get_current_user)) -> list[MarketOut]:
    return [
        MarketOut(
            code=m.value,
            label=m.label,
            currency=m.currency,
            currency_symbol=currency_symbol(m.currency),
        )
        for m in Market
    ]


@router.get("/bars", response_model=BarsResponse, summary="日线 K 线")
def get_bars(
    symbol: str = Query(..., min_length=1, max_length=24, description="规范标的，如 CN:600519"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    refresh: bool = Query(default=False, description="强制重新拉取"),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> BarsResponse:
    inst = _instrument(symbol)
    end_date = _parse_date(end, date.today())
    start_date = _parse_date(start, end_date - timedelta(days=365 * 3))
    try:
        frame = ctx.data.bars(inst.id, start_date, end_date, refresh=refresh)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    info = ctx.data.info(inst.id)
    bars = [
        {
            "date": b.dt.date().isoformat(),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "amount": b.amount,
            "prev_close": b.prev_close,
            "limit_up": b.limit_up,
            "limit_down": b.limit_down,
        }
        for b in frame_to_bars(frame, inst.id)
    ]
    return BarsResponse(
        symbol=inst.id,
        code=inst.code,
        market=inst.market.value,
        currency=inst.market.currency,
        name=info.name,
        bars=bars,
        source=ctx.provider_name,
        cached=ctx.data.store.has_cache(inst.id),
    )


@router.get("/quote", response_model=QuoteResponse, summary="实时快照（多源聚合）")
def get_quote(
    symbols: str = Query(..., description="逗号分隔的标的，最多 50 个"),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> QuoteResponse:
    ids = []
    for raw in symbols.split(","):
        text = raw.strip()
        if not text:
            continue
        ids.append(_instrument(text).id)
    quotes, note = ctx.data.snapshot(ids[:50])
    return QuoteResponse(
        quotes={code: QuoteOut(**quote.to_dict()) for code, quote in quotes.items()},
        ts=datetime.now(timezone.utc).isoformat(),
        note=note,
    )


@router.get("/search", response_model=list[SymbolOut], summary="标的检索")
def search(
    q: str = Query(default="", max_length=32),
    market: str | None = Query(default=None, description="限定市场：CN / US / HK"),
    limit: int = Query(default=30, ge=1, le=100),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> list[SymbolOut]:
    items = ctx.data.search(q, limit, market=market)
    return [SymbolOut(**item.to_dict()) for item in items]


@router.get("/calendar", response_model=list[str], summary="交易日历")
def calendar(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    market: str = Query(default="CN", description="CN 为真实日历，US/HK 为工作日近似"),
    _: User = Depends(get_current_user),
) -> list[str]:
    end_date = _parse_date(end, date.today())
    start_date = _parse_date(start, end_date - timedelta(days=90))
    return [d.isoformat() for d in trading_days(start_date, end_date, market=market)]


@router.get("/cache", response_model=list[DataCacheOut], summary="本地行情缓存")
def cache_stats(
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> list[DataCacheOut]:
    return [DataCacheOut(**row) for row in ctx.data.store.stats()]


@router.post("/refresh", response_model=dict, summary="刷新指定标的缓存")
def refresh_cache(
    symbols: str = Query(..., description="逗号分隔"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> dict:
    ids = [_instrument(s.strip()).id for s in symbols.split(",") if s.strip()][:20]
    end_date = _parse_date(end, date.today())
    start_date = _parse_date(start, end_date - timedelta(days=365 * 5))
    result = ctx.data.store.refresh_many(ids, start_date, end_date)
    return {"refreshed": result}


@router.get("/symbols", response_model=list[str], summary="已缓存标的")
def cached_symbols(ctx: AppContext = Depends(get_ctx), _: User = Depends(get_current_user)) -> list[str]:
    return ctx.data.store.cached_symbols()


__all__ = ["router"]
