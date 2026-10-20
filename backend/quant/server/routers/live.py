"""实盘路由：HTTP 薄层；三重闸门与引擎编排在 services.live_service。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...execution.adapters import ADAPTERS
from ...services import live_service
from ...storage.models import User
from ...storage.repositories.runs import RunRepository
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import (
    LiveAdapterOut,
    LiveStartRequest,
    LiveStateOut,
    Message,
    RunAccepted,
    RunSummary,
    SwitchStrategyRequest,
)

router = APIRouter(prefix="/live", tags=["实盘"])

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
    result = live_service.start(ctx, user_id=user.id, body=body)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.start",
        target=result["run_id"],
        detail={
            "adapter": body.adapter,
            "sandbox": body.sandbox,
            "credential": result["credential_name"],
            "symbols": body.symbols,
            "strategy": body.strategy_id,
        },
    )
    return RunAccepted(run_id=result["run_id"], status="running", mode=result["mode"])


@router.post("/stop", response_model=Message, summary="停止实盘会话（可指定凭证）")
def stop_live(
    credential_id: int | None = Query(default=None, description="不传则停止该用户全部实盘会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    result = live_service.stop(ctx, user_id=user.id, credential_id=credential_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.stop",
        target=result["run_id"] or "all",
        detail={"count": result["stopped"], "credential_id": credential_id},
    )
    return Message(
        message=f"已停止 {result['stopped']} 个实盘会话（挂单已逐单撤销）",
        detail={
            "stopped": result["stopped"],
            "credential_id": credential_id,
            "open_orders": result["open_orders"],
        },
    )


@router.post("/pause", response_model=Message, summary="暂停实盘会话（可继续）")
def pause_live(
    credential_id: int | None = Query(default=None, description="不传则暂停最近一个运行中的会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    live_service.pause(ctx, user_id=user.id, credential_id=credential_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.pause",
        target=str(credential_id) if credential_id else "latest",
        detail={},
    )
    return Message(message="实盘已暂停，可随时继续", detail={"credential_id": credential_id})


@router.post("/resume", response_model=Message, summary="继续已暂停的实盘会话")
def resume_live(
    credential_id: int | None = Query(default=None, description="不传则继续最近一个会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    live_service.resume(ctx, user_id=user.id, credential_id=credential_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.resume",
        target=str(credential_id) if credential_id else "latest",
        detail={},
    )
    return Message(message="实盘已继续", detail={"credential_id": credential_id})


@router.get("/sessions", response_model=list[LiveStateOut], summary="运行中的实盘会话")
def live_sessions(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return [LiveStateOut(**state) for state in live_service.sessions(ctx, user_id=user.id)]


@router.post("/switch", response_model=Message, summary="切换实盘策略（下一轮生效）")
def switch_live(
    body: SwitchStrategyRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    result = live_service.switch(
        ctx,
        user_id=user.id,
        strategy_id=body.strategy_id,
        params=body.params,
        cancel_orders=body.cancel_open_orders,
    )
    engine = ctx.live.engine(user.id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="live.switch_strategy",
        target=engine.config.run_id if engine else "latest",
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
    state = live_service.state(ctx, user_id=user.id, credential_id=credential_id)
    return LiveStateOut(**state) if state is not None else None


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
