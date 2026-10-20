"""回测路由。

``POST /api/backtests`` 立即返回 ``run_id``，实际计算在后台线程；
前端通过 WebSocket 收进度，或轮询 ``GET /api/backtests/{id}``。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...services.backtest_service import build_engine_config
from ...services.custom_params import inject_custom_spec
from ...storage.models import User
from ...storage.repositories import RunRepository
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import BacktestRequest, Message, RunAccepted, RunDetail, RunSummary

router = APIRouter(prefix="/backtests", tags=["回测"])
log = logging.getLogger(__name__)


@router.post("", response_model=RunAccepted, status_code=202, summary="提交回测")
def create_backtest(
    body: BacktestRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.backtests.can_accept():
        raise HTTPException(
            status_code=429,
            detail=f"并发回测已达上限（{ctx.settings.max_concurrent_backtests}），请稍后再试",
        )
    try:
        resolved_params = inject_custom_spec(body.params, user_id=user.id, session=session)
        config = build_engine_config(
            body,
            user_id=user.id,
            default_cash=ctx.settings.default_initial_cash,
            rf=ctx.settings.default_risk_free_rate,
            params=resolved_params,
        )
        run_id = ctx.backtests.submit(config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audit(
        session,
        request=None,
        actor=user.username,
        action="backtest.start",
        target=run_id,
        detail={"strategy": body.strategy_id, "symbols": body.symbols},
    )
    return RunAccepted(run_id=run_id)


@router.get("", response_model=list[RunSummary], summary="回测列表")
def list_backtests(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    mine: bool = Query(default=True, description="只看自己的"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    records = RunRepository(session).list(
        user_id=user.id if mine else None, kind="backtest", limit=limit, offset=offset
    )
    return [RunSummary(**r.to_summary()) for r in records]


@router.get("/{run_id}", response_model=RunDetail, summary="回测详情（含权益曲线与成交）")
def get_backtest(
    run_id: str,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record = RunRepository(session).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    if record.user_id not in (None, user.id) and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问该回测")
    return RunDetail(**record.to_detail())


@router.post("/{run_id}/cancel", response_model=Message, summary="取消运行中的回测")
def cancel_backtest(
    run_id: str,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record = RunRepository(session).get(run_id)
    if record is not None and record.user_id not in (None, user.id) and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权操作该回测")
    if not ctx.backtests.cancel(run_id):
        raise HTTPException(status_code=409, detail="该回测不在运行中")
    return Message(message="已请求取消")


@router.delete("/{run_id}", response_model=Message, summary="删除回测记录")
def delete_backtest(
    run_id: str,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record = RunRepository(session).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    if record.user_id not in (None, user.id) and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除该回测")
    RunRepository(session).delete(run_id)
    return Message(message="已删除")


__all__ = ["router"]
