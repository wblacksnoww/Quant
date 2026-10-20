"""纸盘路由：启动/暂停/继续/停止/实时状态。

同一用户可同时运行多个会话（各有独立虚拟账户与线程），用 ``run_id`` 区分；
「暂停」保留账户、持仓与策略进度，「继续」从暂停处接着跑；「停止」结束会话后，
仍可从历史记录「继续」（恢复现金、持仓、成交与回放进度，沿用同一 run_id）。
会话事件在 WebSocket 层按用户定向投递，不会广播给其他人。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.events import EventBus
from ...engine.base import EngineConfig
from ...engine.paper import PaperEngine, PaperOptions, PaperResumeState
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
            "risk": config.risk,
            "cost": config.cost,
            "benchmark": config.benchmark,
            "risk_free_rate": config.risk_free_rate,
            "replay_days": body.replay_days,
            "replay_interval": body.replay_interval,
            "poll_interval": body.poll_interval,
            "respect_market_hours": body.respect_market_hours,
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


@router.post(
    "/continue",
    response_model=RunAccepted,
    status_code=202,
    summary="从停止处继续历史纸盘会话（同一 run_id）",
)
def resume_paper(
    run_id: str = Query(..., description="历史纸盘运行的 run_id"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record = RunRepository(session).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if record.kind != "paper":
        raise HTTPException(status_code=422, detail="只有纸盘会话可以从停止处继续")
    if record.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权继续该会话")
    if ctx.paper.running(user.id, run_id):
        raise HTTPException(status_code=409, detail="该会话仍在运行")
    if record.status not in ("running", "stopped", "finished", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"状态 {record.status} 不支持继续")

    stored = dict(record.config or {})
    symbols = list(record.symbols or stored.get("symbols") or [])
    strategy_id = record.strategy_id or stored.get("strategy_id") or ""
    mode = str(stored.get("mode") or "replay")
    if mode not in ("replay", "live"):
        mode = "replay"
    params = dict(stored.get("params") or {})
    initial_cash = float(stored.get("initial_cash") or ctx.settings.default_initial_cash)

    equity = [item for item in (record.equity_curve or []) if isinstance(item, dict)]
    if not equity:
        raise HTTPException(status_code=409, detail="该会话没有可恢复的进度")
    try:
        last_date = datetime.fromisoformat(str(equity[-1].get("dt"))).date()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="无法解析上次推进的日期") from exc

    today = date.today()
    warmup_days = int(stored.get("replay_days") or 120)
    if mode == "replay":
        if last_date >= today:
            raise HTTPException(
                status_code=409, detail="该回放已推进到最新交易日，请重新启动一个新会话"
            )
        replay_days = min(1000, max(10, (today - last_date).days + warmup_days))
    else:
        replay_days = min(1000, max(10, warmup_days))
    start = today - timedelta(days=min(3650, replay_days * 2))

    try:
        config = EngineConfig(
            strategy_id=strategy_id,
            symbols=symbols,
            start=start,
            end=today,
            initial_cash=initial_cash,
            params=params,
            cost=dict(stored.get("cost") or {}),
            risk=stored.get("risk"),
            benchmark=stored.get("benchmark"),
            risk_free_rate=float(stored.get("risk_free_rate") or 0.0),
            run_id=run_id,
            user_id=user.id,
        )
        config.validate()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    trades = [item for item in (record.trades or []) if isinstance(item, dict)]
    positions = [item for item in (record.positions or []) if isinstance(item, dict)]
    cash = initial_cash + sum(float(item.get("cash_flow") or 0.0) for item in trades)
    resume_state = PaperResumeState(
        cash=cash,
        positions=positions,
        last_date=last_date if mode == "replay" else None,
        realized_pnl=sum(float(item.get("realized_pnl") or 0.0) for item in positions),
        total_fees=sum(
            float((item.get("fees") or {}).get("total") or 0.0) for item in trades
        ),
        turnover=sum(float(item.get("gross") or 0.0) for item in trades),
        trade_count=len(trades),
        trades=trades,
    )

    replay_interval = float(stored.get("replay_interval") or 0.35)
    poll_interval = float(stored.get("poll_interval") or 5.0)
    respect_hours = bool(stored.get("respect_market_hours", True))
    run_config = {
        **stored,
        "strategy_id": config.strategy_id,
        "symbols": config.symbols,
        "mode": mode,
        "params": config.params,
        "initial_cash": config.initial_cash,
        "risk": config.risk,
        "cost": config.cost,
        "benchmark": config.benchmark,
        "risk_free_rate": config.risk_free_rate,
        "replay_days": replay_days,
        "replay_interval": replay_interval,
        "poll_interval": poll_interval,
        "respect_market_hours": respect_hours,
        "resume_count": int(stored.get("resume_count") or 0) + 1,
    }

    recorder = RunRecorder(
        ctx.db,
        run_id,
        user_id=user.id,
        kind="paper",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config=run_config,
        seed={
            "equity": equity,
            "trades": trades,
            "orders": list(record.orders_data or []),
            "logs": list(record.logs or []),
            "elapsed_seconds": record.elapsed_seconds,
        },
    )
    recorder.start()

    bus = EventBus(f"paper:{run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="paper", run_id=run_id, user_id=user.id)
    engine = PaperEngine(
        config,
        ctx.data,
        options=PaperOptions(
            mode=mode,
            poll_interval=poll_interval,
            replay_days=replay_days,
            replay_interval=replay_interval,
            respect_market_hours=respect_hours,
        ),
        bus=bus,
        recorder=recorder,
        resume=resume_state,
    )

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
        action="paper.resume",
        target=run_id,
        detail={
            "last_date": last_date.isoformat(),
            "mode": mode,
            "resume_count": run_config["resume_count"],
        },
    )
    return RunAccepted(run_id=run_id, status="running", mode=mode)


@router.post("/pause", response_model=Message, summary="暂停纸盘会话（可继续）")
def pause_paper(
    run_id: str | None = Query(default=None, description="不传则暂停最近一个运行中的会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.paper.pause(user.id, run_id):
        raise HTTPException(status_code=409, detail="没有可暂停的纸盘会话")
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.pause",
        target=run_id or "latest",
        detail={},
    )
    return Message(message="纸盘已暂停，可随时继续", detail={"run_id": run_id})


@router.post("/resume", response_model=Message, summary="继续已暂停的纸盘会话")
def resume_paper(
    run_id: str | None = Query(default=None, description="不传则继续最近一个会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.paper.resume(user.id, run_id):
        raise HTTPException(status_code=409, detail="没有已暂停的纸盘会话")
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.resume",
        target=run_id or "latest",
        detail={},
    )
    return Message(message="纸盘已继续", detail={"run_id": run_id})


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
