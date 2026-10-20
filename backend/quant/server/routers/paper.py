"""纸盘路由：HTTP 薄层（校验、审计、响应），业务流程在 services.paper_service。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...services import paper_service
from ...storage.models import User
from ...storage.repositories.runs import RunRepository
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import (
    Message,
    PaperStartRequest,
    PaperStateOut,
    RunAccepted,
    RunSummary,
    SwitchStrategyRequest,
)

router = APIRouter(prefix="/paper", tags=["纸盘"])


@router.post("/start", response_model=RunAccepted, status_code=202, summary="启动纸盘会话")
def start_paper(
    body: PaperStartRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    run_id = paper_service.start(ctx, user_id=user.id, body=body)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.start",
        target=run_id,
        detail={"strategy": body.strategy_id, "mode": body.mode, "symbols": body.symbols},
    )
    return RunAccepted(run_id=run_id, status="running")


@router.post("/stop", response_model=Message, summary="停止纸盘会话（可指定 run_id）")
def stop_paper(
    run_id: str | None = Query(default=None, description="不传则停止该用户全部纸盘会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    stopped = paper_service.stop(ctx, user_id=user.id, run_id=run_id)
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
def continue_paper(
    run_id: str = Query(..., description="历史纸盘运行的 run_id"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    result = paper_service.continue_run(ctx, user_id=user.id, run_id=run_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.resume",
        target=run_id,
        detail={
            "last_date": result["last_date"],
            "mode": result["mode"],
            "resume_count": result["resume_count"],
        },
    )
    return RunAccepted(run_id=run_id, status="running", mode=result["mode"])


@router.post("/pause", response_model=Message, summary="暂停纸盘会话（可继续）")
def pause_paper(
    run_id: str | None = Query(default=None, description="不传则暂停最近一个运行中的会话"),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    paper_service.pause(ctx, user_id=user.id, run_id=run_id)
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
    paper_service.resume(ctx, user_id=user.id, run_id=run_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.resume_running",
        target=run_id or "latest",
        detail={},
    )
    return Message(message="纸盘已继续", detail={"run_id": run_id})


@router.delete("/runs/{run_id}", response_model=Message, summary="删除历史纸盘记录")
def delete_paper_run(
    run_id: str,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    status = paper_service.delete(ctx, user_id=user.id, run_id=run_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.delete",
        target=run_id,
        detail={"status": status},
    )
    return Message(message="已删除历史会话", detail={"run_id": run_id})


@router.get("/sessions", response_model=list[PaperStateOut], summary="运行中的纸盘会话")
def paper_sessions(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return [PaperStateOut(**state) for state in paper_service.sessions(ctx, user_id=user.id)]


@router.post("/switch", response_model=Message, summary="切换运行中的策略（下一轮生效）")
def switch_paper(
    body: SwitchStrategyRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    result = paper_service.switch(
        ctx,
        user_id=user.id,
        strategy_id=body.strategy_id,
        params=body.params,
        cancel_orders=body.cancel_open_orders,
    )
    engine = ctx.paper.engine(user.id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="paper.switch_strategy",
        target=engine.config.run_id if engine else "latest",
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
    state = paper_service.state(ctx, user_id=user.id, run_id=run_id)
    return PaperStateOut(**state) if state is not None else None


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
