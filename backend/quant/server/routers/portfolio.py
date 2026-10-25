"""组合路由：权重优化 + 指数成分股（point-in-time）。

为什么放在一起：这两件事服务同一个问题 ——"回测/实盘到底该买哪些、各买多少"。
成分股负责"哪些"（且必须是当时的成分，否则有幸存者偏差），权重优化负责"多少"。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...portfolio import (
    METHODS,
    PortfolioConstraints,
    PortfolioError,
    align_returns,
    optimize_weights,
)
from ...storage.models import User
from ...storage.repositories.index_membership import IndexMembershipRepository
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import (
    IndexMemberOut,
    IndexSnapshotIn,
    IndexSnapshotOut,
    IndexSummaryOut,
    PortfolioMethodOut,
    PortfolioOptimizeOut,
    PortfolioOptimizeRequest,
)

router = APIRouter(prefix="/portfolio", tags=["组合"])
log = logging.getLogger(__name__)


@router.get("/methods", response_model=list[PortfolioMethodOut], summary="可选优化方法")
def list_methods():
    return [PortfolioMethodOut(key=key, label=label) for key, label in METHODS.items()]


@router.post("/optimize", response_model=PortfolioOptimizeOut, summary="计算组合权重")
def optimize(
    body: PortfolioOptimizeRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    end = body.end or date.today()
    start = body.start or (end - timedelta(days=body.lookback_days))
    if start >= end:
        raise HTTPException(status_code=422, detail="开始日期必须早于结束日期")

    frames = {}
    missing: list[str] = []
    for symbol in body.symbols:
        try:
            frame = ctx.data.bars(symbol, start, end)
        except Exception:  # noqa: BLE001 - 单个标的取数失败只跳过它，不影响其他
            log.warning("组合优化取数失败 %s", symbol, exc_info=True)
            frame = None
        if frame is None or frame.empty:
            missing.append(symbol)
        else:
            frames[symbol] = frame

    if len(frames) < 2:
        raise HTTPException(
            status_code=422,
            detail=f"可用标的不足 2 个（缺失：{', '.join(missing) or '无'}），无法计算组合权重",
        )

    try:
        returns = align_returns(frames)
        solution = optimize_weights(
            returns,
            body.method,
            risk_free_rate=body.risk_free_rate,
            constraints=PortfolioConstraints(min_weight=body.min_weight, max_weight=body.max_weight),
        )
    except PortfolioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = solution.to_dict()
    payload["price_start"] = str(returns.index[0].date())
    payload["price_end"] = str(returns.index[-1].date())
    if missing:
        # 缺数据必须显式说出来：悄悄少算两个标的，权重会全变，用户却以为
        # 拿到的是完整的组合结果。
        payload["note"] = (
            (payload.get("note") or "")
            + ("；" if payload.get("note") else "")
            + f"以下标的因数据缺失被排除：{', '.join(missing)}"
        )

    audit(
        session,
        request=None,
        actor=user.username,
        action="portfolio.optimize",
        target=body.method,
        detail={"symbols": body.symbols, "available": len(frames)},
    )
    return PortfolioOptimizeOut(**payload)


# --------------------------------------------------------------------------- #
# 指数成分股
# --------------------------------------------------------------------------- #


@router.get("/indexes", response_model=list[IndexSummaryOut], summary="已导入成分股的指数")
def list_indexes(
    ctx: AppContext = Depends(get_ctx),
    _user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = IndexMembershipRepository(session)
    return [IndexSummaryOut(**item) for item in repo.list_indexes()]


@router.get(
    "/indexes/{index_code}/members",
    response_model=list[IndexMemberOut],
    summary="某日成分股",
)
def members_at(
    index_code: str,
    as_of: date = Query(default_factory=date.today, description="查询日期"),
    ctx: AppContext = Depends(get_ctx),
    _user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = IndexMembershipRepository(session)
    if not repo.has_history(index_code):
        raise HTTPException(
            status_code=404,
            detail=f"没有导入过 {index_code} 的成分股。请先 POST 一份快照，否则回测只能用当前名单（有幸存者偏差）",
        )
    symbols = repo.members_at(index_code, as_of)
    timeline = {item["symbol"]: item for item in repo.timeline(index_code)}
    return [
        IndexMemberOut(
            symbol=symbol,
            start_date=timeline.get(symbol, {}).get("start_date"),
            end_date=timeline.get(symbol, {}).get("end_date"),
            source=str(timeline.get(symbol, {}).get("source", "manual")),
        )
        for symbol in symbols
    ]


@router.post(
    "/indexes/{index_code}/members",
    response_model=IndexSnapshotOut,
    summary="导入成分股快照",
)
def import_snapshot(
    index_code: str,
    body: IndexSnapshotIn,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = IndexMembershipRepository(session)
    try:
        stats = repo.replace_snapshot(index_code, body.symbols, body.as_of, source=body.source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()

    audit(
        session,
        request=None,
        actor=user.username,
        action="portfolio.import_snapshot",
        target=index_code,
        detail={"as_of": body.as_of.isoformat(), **stats},
    )
    active = len(repo.members_at(index_code, body.as_of))
    return IndexSnapshotOut(
        index_code=index_code,
        added=stats["added"],
        removed=stats["removed"],
        kept=stats["kept"],
        active=active,
        as_of=body.as_of.isoformat(),
    )
