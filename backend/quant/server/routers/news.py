"""新闻/政策路由：录入、列表、PIT 因子查询。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...services import news_service
from ...storage.models import User
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import Message, NewsFactorOut, NewsItemIn, NewsItemOut

router = APIRouter(prefix="/news", tags=["新闻政策"])


@router.post("/items", response_model=NewsItemOut, status_code=201, summary="录入新闻/政策")
def create_news(
    body: NewsItemIn,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    item = news_service.ingest(ctx, user_id=user.id, payload=body)
    audit(
        session,
        request=None,
        actor=user.username,
        action="news.create",
        target=str(item["id"]),
        detail={
            "title": item["title"],
            "symbols": item["symbols"],
            "event_type": item["event_type"],
            "policy_risk": item["policy_risk"],
        },
    )
    return NewsItemOut(**item)


@router.get("/items", response_model=list[NewsItemOut], summary="新闻/政策列表")
def list_news(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
):
    return [NewsItemOut(**item) for item in news_service.list_items(ctx, symbol=symbol, limit=limit)]


@router.get("/factors", response_model=NewsFactorOut, summary="新闻/政策量化因子（PIT）")
def get_factor(
    symbol: str = Query(..., min_length=1, max_length=32),
    window: Literal["15m", "1h", "4h", "1d"] = Query(default="1h"),
    as_of: datetime | None = Query(default=None, description="不传则用当前时间"),
    half_life_hours: float = Query(default=24.0, ge=0.1, le=720.0),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
):
    return NewsFactorOut(
        **news_service.factor(
            ctx,
            symbol=symbol,
            window=window,
            as_of=as_of,
            half_life_hours=half_life_hours,
        )
    )


@router.delete("/items/{news_id}", response_model=Message, summary="删除新闻")
def delete_news(
    news_id: int,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    news_service.delete(ctx, user_id=user.id, news_id=news_id, is_admin=user.role == "admin")
    audit(
        session,
        request=None,
        actor=user.username,
        action="news.delete",
        target=str(news_id),
        detail={},
    )
    return Message(message="已删除新闻", detail={"id": news_id})


__all__ = ["router"]
