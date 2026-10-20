"""行情路由：K 线、实时快照、标的检索、交易日历、缓存管理。

所有接口统一使用规范标的 id（``MARKET:CODE``，如 ``CN:600519`` / ``US:AAPL`` /
``HK:00700``）；兼容输入（裸 6 位代码、``sh600519``、``aapl`` 等）在入口处
由 :func:`quant.core.markets.parse_instrument` 归一化，内部一律只认规范 id。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.errors import DataError, DataNotFound
from ...core.markets import Market, currency_symbol, parse_instrument, price_limits, rules_for
from ...data.base import frame_to_bars
from ...data.calendar import trading_days
from ...storage.models import User
from ..context import AppContext
from ..deps import get_ctx, get_current_user
from ..schemas import (
    BarsResponse,
    DataCacheOut,
    IndexQuoteOut,
    InstrumentOut,
    MarketOut,
    QuoteOut,
    QuoteResponse,
    RankingOut,
    SymbolOut,
)

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


@router.get("/intraday", response_model=BarsResponse, summary="分钟 K 线（今日/本周）")
def get_intraday(
    symbol: str = Query(..., min_length=1, max_length=32, description="规范标的，如 CN:600519"),
    freq: Literal["1m", "5m", "15m", "30m", "60m"] = Query(default="5m"),
    limit: int = Query(default=240, ge=10, le=1000),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> BarsResponse:
    inst = _instrument(symbol)
    try:
        frame = ctx.data.intraday(inst.id, freq=freq, limit=limit)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=422, detail=f"{inst.market.label}暂不支持分钟 K 线（当前支持 A股与虚拟货币）"
        ) from exc
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    source_name = ctx.data.provider_for_market(inst.market.value).name
    bars = [
        {
            "date": b.dt.strftime("%Y-%m-%d %H:%M"),
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
        bars=bars,
        source=f"{source_name}:{freq}",
        cached=False,
    )


@router.get("/klines", response_model=BarsResponse, summary="K 线（任意周期）")
def get_klines(
    symbol: str = Query(..., min_length=1, max_length=32, description="规范标的"),
    interval: Literal["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1M"] = Query(
        default="1d", description="分钟 / 日 / 周 / 月"
    ),
    limit: int = Query(default=240, ge=10, le=1000),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> BarsResponse:
    inst = _instrument(symbol)
    try:
        frame = ctx.data.klines(inst.id, interval=interval, limit=limit)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=422, detail=f"{inst.market.label}暂不支持该周期（分钟线仅 A股/虚拟货币）"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    intraday = interval in ("1m", "5m", "15m", "30m", "60m")
    fmt = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
    source_name = ctx.data.provider_for_market(inst.market.value).name
    bars = [
        {
            "date": b.dt.strftime(fmt),
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
        bars=bars,
        source=f"{source_name}:{interval}",
        cached=False,
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
        ts=datetime.now(UTC).isoformat(),
        note=note,
    )


@router.get("/instrument", response_model=InstrumentOut, summary="标的详情（规则 + 快照）")
def instrument_info(
    symbol: str = Query(..., min_length=1, max_length=32, description="规范标的，如 CN:600519"),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> InstrumentOut:
    inst = _instrument(symbol)
    rules = rules_for(inst.id)
    quotes, _note = ctx.data.snapshot([inst.id])
    quote = quotes.get(inst.id)
    prev_close = quote.prev_close if quote else None
    limit_up, limit_down = price_limits(prev_close or 0.0, inst.id, board=rules)
    return InstrumentOut(
        symbol=inst.id,
        code=inst.code,
        market=inst.market.value,
        currency=inst.market.currency,
        currency_symbol=currency_symbol(inst.market.currency),
        name=quote.name if quote else "",
        board=rules.name,
        limit_pct=rules.limit_pct,
        lot_size=rules.lot_size,
        t_plus_1=rules.t_plus_1,
        price_digits=rules.price_digits,
        price=quote.price if quote else 0.0,
        change=quote.change if quote else None,
        change_pct=quote.change_pct if quote else None,
        prev_close=prev_close,
        volume=quote.volume if quote else None,
        amount=quote.amount if quote else None,
        limit_up=limit_up,
        limit_down=limit_down,
        source=quote.source if quote else "",
    )


@router.get("/indices", response_model=list[IndexQuoteOut], summary="指数行情（卡片）")
def indices(
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> list[IndexQuoteOut]:
    return [IndexQuoteOut(**item) for item in ctx.data.indices()]


@router.get("/ranking", response_model=RankingOut, summary="市场排行榜（今日/本周/本月）")
def ranking(
    market: str = Query(default="CN", description="CN / US / HK / CRYPTO"),
    sort: Literal["change_pct", "change", "amount"] = Query(
        default="change_pct", description="change_pct 涨跌幅 / amount 成交额 / change 涨跌额"
    ),
    order: Literal["desc", "asc"] = Query(default="desc", description="desc 榜前 / asc 榜尾"),
    period: Literal["day", "week", "month"] = Query(
        default="day", description="day 今日 / week 本周 / month 本月"
    ),
    limit: int = Query(default=20, ge=1, le=50),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> RankingOut:
    key = market.strip().upper()
    if key not in {m.value for m in Market}:
        raise HTTPException(status_code=422, detail=f"不支持的市场: {market}")
    items = ctx.data.ranking(key, sort=sort, order=order, limit=limit, period=period)
    note = ""
    if not items:
        note = "当前市场暂未返回榜单数据（数据源可能限流或休市）"
    elif period != "day":
        label = "本周" if period == "week" else "本月"
        note = f"{label}涨跌幅基于当日活跃榜单（成交额+涨跌幅两端）计算（免费源无全市场{label}涨幅）"
    return RankingOut(
        market=key,
        sort=sort,
        order=order,
        period=period,
        items=[QuoteOut(**quote.to_dict()) for quote in items],
        note=note,
        ts=datetime.now(UTC).isoformat(),
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
