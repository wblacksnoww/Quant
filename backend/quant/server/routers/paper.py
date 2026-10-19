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
from ..schemas import (
    Message,
    PaperStartRequest,
    PaperStateOut,
    RunAccepted,
    RunSummary,
    SwitchStrategyRequest,
)
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


@router.post("/stop", response_model=Message, summary="停止纸盘会话（可指定 run_id）")
def stop_paper(
    run_id: str | None = Query(default=None, description="不传则停止该用户全部纸盘会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.paper.running(user.id, run_id):
        raise HTTPException(
            status_code=409,
            detail="没有运行中的纸盘会话" if run_id is None else f"运行 {run_id} 不在运行",
        )
    stopped = ctx.paper.stop(user.id, run_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.stop",
        target=run_id or "all",
        detail={"count": stopped},
    )
    return Message(
        message=f"已停止 {stopped} 个纸盘会话",
        detail={"stopped": stopped, "run_id": run_id},
    )


@router.get("/sessions", response_model=list[PaperStateOut], summary="运行中的纸盘会话")
def paper_sessions(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return [PaperStateOut(**state) for state in ctx.paper.sessions(user.id)]


@router.post("/switch", response_model=Message, summary="切换运行中的策略（下一轮生效）")
def switch_paper(
    body: SwitchStrategyRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.paper.running(user.id):
        raise HTTPException(status_code=409, detail="没有运行中的纸盘会话")
    engine = ctx.paper.engine(user.id)
    if engine is None:  # pragma: no cover
        raise HTTPException(status_code=409, detail="没有运行中的纸盘会话")
    resolved = inject_custom_spec(body.params, user=user, session=session)
    try:
        result = engine.switch_strategy(
            body.strategy_id, resolved, cancel_orders=body.cancel_open_orders
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.switch_strategy",
        target=engine.config.run_id,
        detail=result,
    )
    return Message(
        message=f"策略将从下一轮开始切换为 {body.strategy_id}",
        detail=result,
    )


@router.get("/state", response_model=PaperStateOut | None, summary="纸盘实时状态（可指定 run_id）")
def paper_state(
    run_id: str | None = Query(default=None, description="不传则返回最近一个运行中的会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    state = ctx.paper.state(user.id, run_id)
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
