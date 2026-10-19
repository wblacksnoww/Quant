"""实盘路由：启动/停止/状态。

三重闸门（缺一不可）：

1. ``QUANT_ENABLE_LIVE_TRADING=true`` 服务端总开关；
2. 适配器 ``sandbox`` 默认开启（测试网/模拟账户）；关闭时必须 ``confirm_live=true``；
3. 凭据必须属于当前用户且 ``kind=broker``，使用即审计。

实盘会话同样按用户隔离，同一账号不允许并发（否则两套策略争抢同一笔资金）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.events import EventBus
from ...engine.live import LiveEngine, LiveOptions
from ...execution.adapters import ADAPTERS, create_adapter
from ...storage.models import User
from ...storage.repositories import CredentialRepository, RunRecorder, RunRepository
from ..context import AppContext
from ..custom_params import inject_custom_spec
from ..deps import audit, get_current_user, get_ctx, get_db
from ..schemas import (
    LiveAdapterOut,
    LiveStartRequest,
    LiveStateOut,
    Message,
    RunAccepted,
    RunSummary,
    SwitchStrategyRequest,
)
from ..ws import bus_bridge
from .backtest import build_engine_config

router = APIRouter(prefix="/live", tags=["实盘"])
log = logging.getLogger(__name__)

_ADAPTER_META = {
    "ccxt": {"markets": ["CRYPTO"], "requires_api_key": True},
    "miniqmt": {"markets": ["CN"], "requires_api_key": True},
}


@router.get("/adapters", response_model=list[LiveAdapterOut], summary="可用实盘通道")
def list_adapters(_: User = Depends(get_current_user)) -> list[LiveAdapterOut]:
    out = []
    for name, cls in sorted(ADAPTERS.items()):
        meta = _ADAPTER_META.get(name, {})
        out.append(
            LiveAdapterOut(
                name=name,
                supports_sandbox=cls.supports_sandbox,
                requires_api_key=cls.requires_api_key,
                markets=list(meta.get("markets", [])),
            )
        )
    return out


@router.post("/start", response_model=RunAccepted, status_code=202, summary="启动实盘会话")
def start_live(
    body: LiveStartRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.settings.enable_live_trading:
        raise HTTPException(
            status_code=403,
            detail="实盘功能未开启：请先在服务端设置 QUANT_ENABLE_LIVE_TRADING=true",
        )
    if ctx.live.running(user.id, body.credential_id):
        raise HTTPException(status_code=409, detail="该券商凭证已有实盘会话在运行，请先停止")
    if not body.sandbox and not body.confirm_live:
        raise HTTPException(
            status_code=422,
            detail="关闭测试网意味着使用真实资金，必须同时勾选 confirm_live",
        )

    credential = CredentialRepository(session).get(body.credential_id, owner_id=user.id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭据不存在或不属于你")
    if credential.kind != "broker":
        raise HTTPException(status_code=422, detail="该凭据不是券商凭据（kind 应为 broker）")
    payload = CredentialRepository(session).reveal(credential)

    try:
        adapter = create_adapter(body.adapter, sandbox=body.sandbox, **payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    resolved_params = inject_custom_spec(body.params, user=user, session=session)
    config = build_engine_config(
        body,
        user_id=user.id,
        default_cash=ctx.settings.default_initial_cash,
        rf=ctx.settings.default_risk_free_rate,
        params=resolved_params,
    )
    # 实盘不回溯历史：起止日期只用于指标预热
    end = body.end or date.today()
    config.end = end
    config.start = end - timedelta(days=max(body.warmup_days * 2, 60))

    recorder = RunRecorder(
        ctx.db,
        config.run_id,
        user_id=user.id,
        kind="live",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config={
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "mode": "live",
            "adapter": body.adapter,
            "sandbox": body.sandbox,
            "params": config.params,
        },
    )
    recorder.start()

    bus = EventBus(f"live:{config.run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="live", run_id=config.run_id, user_id=user.id)
    engine = LiveEngine(
        config,
        ctx.data,
        adapter,
        options=LiveOptions(
            poll_interval=body.poll_interval or ctx.settings.live_poll_interval,
            warmup_days=body.warmup_days,
        ),
        bus=bus,
        recorder=recorder,
        credential_id=body.credential_id,
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
        ctx.live.start(user.id, body.credential_id, engine)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    audit(
        session,
        request=None,
        actor=user.username,
        action="live.start",
        target=config.run_id,
        detail={
            "adapter": body.adapter,
            "sandbox": body.sandbox,
            "credential": credential.name,
            "symbols": config.symbols,
            "strategy": config.strategy_id,
        },
    )
    mode = "sandbox" if body.sandbox else "live"
    return RunAccepted(run_id=config.run_id, status="running", mode=mode)


@router.post("/stop", response_model=Message, summary="停止实盘会话（可指定凭证）")
def stop_live(
    credential_id: int | None = Query(default=None, description="不传则停止该用户全部实盘会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.live.running(user.id, credential_id):
        raise HTTPException(
            status_code=409,
            detail="没有运行中的实盘会话" if credential_id is None else "该凭证没有运行中的实盘会话",
        )
    engine = ctx.live.engine(user.id, credential_id)
    stopped = ctx.live.stop(user.id, credential_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.stop",
        target=engine.config.run_id if engine else "all",
        detail={"count": stopped, "credential_id": credential_id},
    )
    return Message(
        message=f"已停止 {stopped} 个实盘会话（挂单已逐单撤销）",
        detail={
            "stopped": stopped,
            "credential_id": credential_id,
            "open_orders": len(engine.broker.open_orders) if engine and engine.broker else 0,
        },
    )


@router.get("/sessions", response_model=list[LiveStateOut], summary="运行中的实盘会话")
def live_sessions(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return [LiveStateOut(**state) for state in ctx.live.sessions(user.id)]


@router.post("/switch", response_model=Message, summary="切换实盘策略（下一轮生效）")
def switch_live(
    body: SwitchStrategyRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not ctx.live.running(user.id):
        raise HTTPException(status_code=409, detail="没有运行中的实盘会话")
    engine = ctx.live.engine(user.id)
    if engine is None:  # pragma: no cover
        raise HTTPException(status_code=409, detail="没有运行中的实盘会话")
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
        action="live.switch_strategy",
        target=engine.config.run_id,
        detail=result,
    )
    return Message(
        message=f"策略将从下一轮开始切换为 {body.strategy_id}",
        detail=result,
    )


@router.get("/state", response_model=LiveStateOut | None, summary="实盘实时状态（可指定凭证）")
def live_state(
    credential_id: int | None = Query(default=None, description="不传则返回最近一个运行中的会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    state = ctx.live.state(user.id, credential_id)
    if state is None:
        return None
    return LiveStateOut(**state)


@router.get("/runs", response_model=list[RunSummary], summary="历史实盘记录")
def live_runs(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    records = RunRepository(session).list(user_id=user.id, kind="live", limit=limit)
    return [RunSummary(**r.to_summary()) for r in records]


__all__ = ["router"]
