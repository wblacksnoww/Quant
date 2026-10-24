"""C++ 交易网关路由：状态 / 启停。

三重闸门复用实盘：QUANT_ENABLE_LIVE_TRADING 总开关、sandbox 默认 + confirm_live、
live 模式必须选择 broker 凭据。paper 模式订单只写网关 JSONL 审计不进交易所。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...storage.models import User
from ..context import AppContext
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import GatewayStartRequest, GatewayStatusOut, Message

router = APIRouter(prefix="/gateway", tags=["网关"])


@router.get("/status", response_model=GatewayStatusOut, summary="网关状态")
def status(
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
):
    return GatewayStatusOut(**ctx.gateway_manager.status())


@router.post("/start", response_model=GatewayStatusOut, status_code=202, summary="启动网关")
def start(
    body: GatewayStartRequest,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not body.sandbox:
        if not ctx.settings.enable_live_trading:
            from ...core.errors import PermissionDenied

            raise PermissionDenied("真实资金网关需要先在服务端设置 QUANT_ENABLE_LIVE_TRADING=true")
        if not body.confirm_live:
            from ...core.errors import InvalidRequest

            raise InvalidRequest("真实资金模式必须勾选 confirm_live")

    result = ctx.gateway_manager.start(
        user_id=user.id,
        strategy_type=body.strategy_type,
        params=body.params,
        symbols=body.symbols,
        sandbox=body.sandbox,
        credential_id=body.credential_id,
        bar_interval_sec=body.bar_interval_sec,
        risk_rules=[r.model_dump(exclude_none=True) for r in body.risk] if body.risk else None,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="gateway.start",
        target=f"gateway:{body.strategy_type}",
        detail={"mode": result["mode"], "symbols": result["symbols"], "sandbox": body.sandbox},
    )
    return GatewayStatusOut(**ctx.gateway_manager.status())


@router.post("/stop", response_model=Message, summary="停止网关")
def stop(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    ctx.gateway_manager.stop()
    audit(
        session,
        request=None,
        actor=user.username,
        action="gateway.stop",
        target="gateway",
        detail={},
    )
    return Message(message="网关已停止")


__all__ = ["router"]
