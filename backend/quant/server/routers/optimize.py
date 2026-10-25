"""参数寻优路由。

``POST /api/optimize`` 立即返回 ``job_id``，计算在后台线程；前端轮询
``GET /api/optimize/{job_id}`` 拿进度与结果（寻优是几十秒到几分钟的长任务，
用 WebSocket 推千分位进度收益不大，轮询更简单）。

同一时刻只允许一个任务：寻优是纯 CPU 密集的，并发只会让所有任务一起变慢。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...optimize import OBJECTIVES, SpaceError, WalkForwardError
from ...optimize.space import MAX_COMBINATIONS, count_combinations
from ...services.optimize_service import OptimizeService
from ...storage.models import User
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import Message, OptimizeJobOut, OptimizeJobSummary, OptimizeStartRequest

router = APIRouter(prefix="/optimize", tags=["参数寻优"])
log = logging.getLogger(__name__)


def _service(ctx: AppContext) -> OptimizeService:
    service = getattr(ctx, "optimize", None)
    if service is None:
        raise HTTPException(status_code=503, detail="寻优服务未就绪")
    return service


@router.get("/objectives", summary="可选的寻优目标")
def list_objectives():
    return [{"key": key, "label": label} for key, label in OBJECTIVES.items()]


@router.post("", response_model=OptimizeJobSummary, status_code=202, summary="提交寻优任务")
def create_job(
    body: OptimizeStartRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    from ...optimize import OptimizeRequest

    options = body.walk_forward_options
    request = OptimizeRequest(
        strategy_id=body.strategy_id,
        symbols=body.symbols,
        start=body.start,
        end=body.end,
        space=body.space,
        objective=body.objective,
        initial_cash=body.initial_cash,
        cost=body.cost.model_dump() if body.cost else {},
        risk=[rule.model_dump() for rule in body.risk] if body.risk else None,
        benchmark=body.benchmark,
        base_params=body.params,
        min_trades=body.min_trades,
        method=body.method,
        sample_size=body.sample_size,
        seed=body.seed,
        train_days=options.train_days,
        test_days=options.test_days,
        step_days=options.step_days,
    )
    try:
        request.validate()
        # 组合数必须在提交前校验：放在后台线程里抛，前端只会收到 202 +
        # 一个稍后失败的任务，用户根本不知道自己把空间填太大了。
        total = count_combinations(body.space)
        if total > MAX_COMBINATIONS:
            raise SpaceError(
                f"参数组合共 {total} 组，超过上限 {MAX_COMBINATIONS}。"
                "请缩小取值范围或减少参数个数 —— 组合过多时选出的“最优参数”基本是历史噪音。"
            )
    except (ValueError, SpaceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    mode = "walk_forward" if body.walk_forward else "search"
    service = _service(ctx)
    try:
        job_id = service.submit(request, mode=mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (SpaceError, WalkForwardError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audit(
        session,
        request=None,
        actor=user.username,
        action="optimize.start",
        target=job_id,
        detail={
            "mode": mode,
            "strategy": body.strategy_id,
            "objective": body.objective,
            "max_combinations": MAX_COMBINATIONS,
        },
    )
    job = service.get(job_id)
    return OptimizeJobSummary(**job.to_summary())  # type: ignore[union-attr]


@router.get("", response_model=list[OptimizeJobSummary], summary="寻优任务列表")
def list_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    ctx: AppContext = Depends(get_ctx),
    _user: User = Depends(get_current_user),
):
    return [OptimizeJobSummary(**job.to_summary()) for job in _service(ctx).list(limit)]


@router.get("/{job_id}", response_model=OptimizeJobOut, summary="寻优任务详情")
def get_job(
    job_id: str,
    trials: int = Query(default=100, ge=1, le=2000, description="返回前 N 组参数"),
    ctx: AppContext = Depends(get_ctx),
    _user: User = Depends(get_current_user),
):
    job = _service(ctx).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="寻优任务不存在")
    return OptimizeJobOut(**job.to_full(trial_limit=trials))


@router.post("/{job_id}/cancel", response_model=Message, summary="取消寻优任务")
def cancel_job(
    job_id: str,
    ctx: AppContext = Depends(get_ctx),
    _user: User = Depends(get_current_user),
):
    if not _service(ctx).cancel(job_id):
        raise HTTPException(status_code=409, detail="任务不存在或已结束")
    return Message(message="已发送取消请求")
