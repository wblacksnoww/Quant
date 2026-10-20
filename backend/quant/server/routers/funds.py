"""场外基金路由：净值、持仓、申赎流水。

**不走事件驱动引擎**：场外基金按净值确认份额，没有盘中撮合。
这里直接以「申赎流水 → 持仓聚合」的方式记账（见 :mod:`quant.funds`），
所有数据按用户隔离，写操作留审计。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ...core.errors import DataError, DataNotFound
from ...core.markets import Market, parse_instrument
from ...data.providers.fund_provider import FundProvider
from ...funds import SIDE_SUBSCRIBE, compute_positions
from ...storage.models import User
from ...storage.repositories import FundTradeRepository
from ..deps import audit, get_current_user, get_db
from ..schemas import (
    FundHoldingOut,
    FundNavOut,
    FundNavPoint,
    FundPortfolioOut,
    FundTradeCreate,
    FundTradeOut,
    SymbolOut,
)

router = APIRouter(prefix="/funds", tags=["基金"])
log = logging.getLogger(__name__)

_provider: FundProvider | None = None


def get_provider() -> FundProvider:
    """进程级单例（内部自带 TTL 缓存；实例化不联网）。"""
    global _provider
    if _provider is None:
        _provider = FundProvider()
    return _provider


def _parse_day(value: str | None, default: date | None = None) -> date:
    if not value:
        if default is None:
            raise HTTPException(status_code=422, detail="日期不能为空")
        return default
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式应为 YYYY-MM-DD: {value}") from exc


def _fund_symbol(symbol: str) -> str:
    try:
        inst = parse_instrument(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if inst.market is not Market.FUND:
        raise HTTPException(status_code=422, detail=f"不是场外基金标的（需 FUND: 前缀）: {symbol}")
    return inst.id


def _fund_name(code_or_symbol: str) -> str:
    try:
        wanted = _fund_symbol(code_or_symbol)
        code = parse_instrument(wanted).code
        for item_code, name, _ in get_provider().fund_list():
            if item_code == code:
                return name
    except Exception:  # noqa: BLE001 - 名称只是展示，取不到不阻塞
        return ""
    return ""


@router.get("/search", response_model=list[SymbolOut], summary="基金检索（代码/简称）")
def search_funds(
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=30, ge=1, le=100),
    _: User = Depends(get_current_user),
) -> list[SymbolOut]:
    try:
        items = get_provider().search(q, limit, market="FUND")
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [SymbolOut(**item.to_dict()) for item in items]


@router.get("/nav", response_model=FundNavOut, summary="基金历史净值")
def fund_nav(
    symbol: str = Query(..., min_length=1, max_length=32),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    _: User = Depends(get_current_user),
) -> FundNavOut:
    code = _fund_symbol(symbol)
    today = date.today()
    start_day = _parse_day(start, today.replace(year=today.year - 3))
    end_day = _parse_day(end, today)
    if start_day > end_day:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    try:
        points = get_provider().nav_series(code, start_day, end_day)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FundNavOut(
        symbol=code,
        name=_fund_name(code),
        points=[FundNavPoint(**p) for p in points],
    )


@router.get("/holdings", response_model=FundPortfolioOut, summary="基金持仓与收益")
def fund_holdings(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FundPortfolioOut:
    trades = FundTradeRepository(session).list_for(user.id, limit=None)
    summary = compute_positions(trades)
    navs: dict[str, float] = {}
    missing: list[str] = []
    for symbol in summary.positions:
        try:
            nav = get_provider().latest_nav(symbol)
        except Exception:  # noqa: BLE001 - 净值源不可用时按成本展示
            nav = None
        if nav:
            navs[symbol] = nav
        else:
            missing.append(symbol)
    rows = []
    for symbol, pos in summary.positions.items():
        payload = pos.to_dict(navs.get(symbol))
        payload["name"] = payload["name"] or _fund_name(symbol)
        rows.append(FundHoldingOut(**payload))
    note = f"{len(missing)} 只基金净值暂不可用，市值按成本计" if missing else ""
    return FundPortfolioOut(holdings=rows, totals=summary.totals(navs), nav_note=note)


@router.get("/trades", response_model=list[FundTradeOut], summary="申赎流水")
def list_trades(
    symbol: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[FundTradeOut]:
    code = _fund_symbol(symbol) if symbol else None
    records = FundTradeRepository(session).list_for(user.id, symbol=code, limit=limit)
    return [FundTradeOut(**r.to_dict()) for r in records]


@router.post("/trades", response_model=FundTradeOut, status_code=201, summary="记一笔申购/赎回")
def create_trade(
    body: FundTradeCreate,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FundTradeOut:
    code = _fund_symbol(body.symbol)
    repo = FundTradeRepository(session)
    trade_day = _parse_day(body.trade_date, date.today())
    confirm_day = _parse_day(body.confirm_date) if body.confirm_date else None
    if confirm_day is not None and confirm_day < trade_day:
        raise HTTPException(status_code=422, detail="确认日不能早于申请日")

    provider = get_provider()
    nav = body.nav
    if nav is None:
        try:
            nav = provider.nav_on(code, trade_day)
        except DataError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not nav:
            raise HTTPException(status_code=422, detail="该日期尚无净值，请手动填写成交净值")
    nav = float(nav)

    if body.side == SIDE_SUBSCRIBE:
        if not body.amount:
            raise HTTPException(status_code=422, detail="申购需要填写金额")
        fee = body.amount * body.fee_rate
        shares = (body.amount - fee) / nav
        amount = body.amount
        if shares <= 0:
            raise HTTPException(status_code=422, detail="扣除申购费后金额不足")
    else:
        if not body.shares:
            raise HTTPException(status_code=422, detail="赎回需要填写份额")
        held = compute_positions(repo.list_for(user.id, symbol=code, limit=None))
        pos = held.positions.get(code)
        available = pos.shares if pos else 0.0
        if body.shares > available + 1e-9:
            raise HTTPException(
                status_code=422,
                detail=f"可赎回份额不足：持有 {available:.4f}，申报 {body.shares:.4f}",
            )
        shares = body.shares
        fee = shares * nav * body.fee_rate
        amount = shares * nav - fee

    record = repo.create(
        user_id=user.id,
        symbol=code,
        name=_fund_name(code),
        side=body.side,
        trade_date=datetime.combine(trade_day, time.min),
        confirm_date=datetime.combine(confirm_day, time.min) if confirm_day else None,
        nav=nav,
        shares=shares,
        amount=amount,
        fee_rate=body.fee_rate,
        fee=fee,
        note=body.note,
    )
    audit(
        session,
        request,
        actor=user.username,
        action=f"fund.{body.side}",
        target=code,
        detail={
            "trade_id": record.id,
            "nav": nav,
            "shares": round(shares, 4),
            "amount": round(amount, 2),
            "fee": round(fee, 2),
        },
    )
    # 显式提交：前端在 201 后会立刻刷新持仓，不能等依赖清理时才落库
    session.commit()
    return FundTradeOut(**record.to_dict())


@router.delete("/trades/{trade_id}", status_code=204, summary="删除一笔流水（补录错误修正）")
def delete_trade(
    trade_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    repo = FundTradeRepository(session)
    record = repo.get(trade_id, user_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="流水不存在")
    detail: dict[str, Any] = {
        "symbol": record.symbol,
        "side": record.side,
        "shares": round(record.shares, 4),
        "amount": round(record.amount, 2),
    }
    repo.delete(record)
    audit(
        session,
        request,
        actor=user.username,
        action="fund.delete_trade",
        target=record.symbol,
        detail=detail,
    )
    # 同理：删除后前端会立刻重算持仓
    session.commit()


__all__ = ["router"]
