"""纸盘路由：启动/停止/实时状态。

**每个用户一份独立会话**：``PaperManager`` 为每个用户维护一个后台引擎线程，
同一用户同时只允许一个会话，不同用户互不影响；会话事件在 WebSocket 层
按用户定向投递，不会广播给其他人。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.events import EventBus
from ...engine.paper import PaperEngine, PaperOptions
from ...storage.models import User
from ...storage.repositories import RunRecorder, RunRepository
from ..context import AppContext
from ..custom_params import inject_custom_spec
from ..deps import audit, get_current_user, get_ctx, get_db
from ..schemas import Message, PaperStartRequest, PaperStateOut, RunAccepted, RunSummary
from ..ws import bus_bridge
from .backtest import build_engine_config

router = APIRouter(prefix="/paper", tags=["纸盘"])
log = logging.getLogger(__name__)


@router.post("/start", response_model=RunAccepted, status_code=202, summary="启动纸盘会话")
def start_paper(
    body: PaperStartRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if ctx.paper.running(user.id):
        raise HTTPException(status_code=409, detail="你已有纸盘会话在运行，请先停止")

    resolved_params = inject_custom_spec(body.params, user=user, session=session)
    config = build_engine_config(
        body,
        user_id=user.id,
        default_cash=ctx.settings.default_initial_cash,
        rf=ctx.settings.default_risk_free_rate,
        params=resolved_params,
    )
    recorder = RunRecorder(
        ctx.db,
        config.run_id,
        user_id=user.id,
        kind="paper",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config={
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "mode": body.mode,
            "params": config.params,
            "initial_cash": config.initial_cash,
        },
    )
    recorder.start()

    bus = EventBus(f"paper:{config.run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="paper", run_id=config.run_id, user_id=user.id)

    engine = PaperEngine(
        config,
        ctx.data,
        options=PaperOptions(
            mode=body.mode,
            poll_interval=body.poll_interval,
            replay_days=body.replay_days,
            replay_interval=body.replay_interval,
            respect_market_hours=body.respect_market_hours,
        ),
        bus=bus,
        recorder=recorder,
    )

    # 纸盘结束后释放事件桥
    original_run = engine.run

    def _run_with_cleanup():
        try:
            return original_run()
        finally:
            for cancel in unwire:
                cancel()

    engine.run = _run_with_cleanup  # type: ignore[method-assign]

    try:
        ctx.paper.start(user.id, engine)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.start",
        target=config.run_id,
        detail={"strategy": config.strategy_id, "mode": body.mode, "symbols": config.symbols},
    )
    return RunAccepted(run_id=config.run_id, status="running")


@router.post("/stop", response_model=Message, summary="停止纸盘会话")
def stop_paper(
    request_body: dict | None = None,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    engine = ctx.paper.engine(user.id)
    if engine is None:
        raise HTTPException(status_code=409, detail="没有运行中的纸盘会话")
    stopped = ctx.paper.stop(user.id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.stop",
        target=engine.config.run_id,
    )
    return Message(
        message="纸盘已停止" if stopped else "已发送停止信号，等待当前轮询结束",
        detail={"stopped": stopped},
    )


@router.get("/state", response_model=PaperStateOut | None, summary="纸盘实时状态（仅自己的会话）")
def paper_state(ctx: AppContext = Depends(get_ctx), user: User = Depends(get_current_user)):
    state = ctx.paper.state(user.id)
    if state is None:
        return None
    return PaperStateOut(**state)


@router.get("/runs", response_model=list[RunSummary], summary="历史纸盘记录")
def paper_runs(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    records = RunRepository(session).list(user_id=user.id, kind="paper", limit=limit)
    return [RunSummary(**r.to_summary()) for r in records]


__all__ = ["router"]
