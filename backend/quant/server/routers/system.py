"""系统路由：健康检查、运行信息、加密状态、审计链校验、数据缓存。"""

from __future__ import annotations

import logging
import platform
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...data.base import available_providers
from ...security.password import algorithm as password_algorithm
from ...storage.models import User
from ...storage.repositories import AuditRepository, UserRepository
from ...strategy.registry import strategy_ids
from ..context import AppContext
from ..deps import get_ctx, get_current_user, get_db
from ..schemas import AuditOut, AuditVerifyOut, DataCacheOut, HealthOut, SystemInfo

router = APIRouter(prefix="/system", tags=["系统"])
log = logging.getLogger(__name__)


@router.get("/health", response_model=HealthOut, summary="健康检查（无需认证）")
def health(ctx: AppContext = Depends(get_ctx)) -> HealthOut:
    return HealthOut(
        status="ok" if ctx.db.healthcheck() else "degraded",
        version=ctx.settings.version,
        env=ctx.settings.env,
        database=ctx.db.healthcheck(),
        uptime_seconds=round(ctx.uptime, 1),
    )


@router.get("/info", response_model=SystemInfo, summary="系统与安全信息")
def info(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> SystemInfo:
    settings = ctx.settings
    totp_users = int(
        session.scalar(select(func.count()).select_from(User).where(User.totp_enabled.is_(True))) or 0
    )
    total_users = UserRepository(session).count()
    db_kind = settings.database_url.split(":", 1)[0]
    return SystemInfo(
        name=settings.app_name,
        version=settings.version,
        env=settings.env,
        python=platform.python_version(),
        database=db_kind,
        data_provider=ctx.provider_name,
        available_providers=available_providers(),
        encryption={
            "field_cipher": "AES-256-GCM",
            "kdf": "HKDF-SHA256",
            "key_source": ctx.master.source.split(":", 1)[0],
            "key_fingerprint": ctx.master.fingerprint,
            "password_hash": password_algorithm(),
            "jwt_algorithm": "HS256",
            "transit": "TLS（由反向代理终结）",
            "audit_chain": "SHA-256 哈希链",
        },
        auth={
            "access_ttl": settings.access_token_ttl,
            "refresh_ttl": settings.refresh_token_ttl,
            "require_totp": settings.require_totp,
            "totp_users": totp_users,
            "total_users": total_users,
            "login_max_attempts": settings.login_max_attempts,
        },
        paper_running=ctx.paper.running(user.id),
        strategies=strategy_ids(),
        warnings=ctx.warnings,
    )


@router.get("/audit", response_model=list[AuditOut], summary="审计日志")
def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rows = AuditRepository(session).list(limit=limit, offset=offset, action=action)
    return [AuditOut(**r.to_dict()) for r in rows]


@router.get("/audit/verify", response_model=AuditVerifyOut, summary="校验审计链完整性")
def verify_audit(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    valid, checked, message = AuditRepository(session).verify()
    return AuditVerifyOut(valid=valid, checked=checked, message=message or "哈希链完整")


@router.get("/data", response_model=list[DataCacheOut], summary="行情缓存概览")
def data_cache(
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> list[DataCacheOut]:
    return [DataCacheOut(**row) for row in ctx.data.store.stats()]


@router.post("/data/refresh", response_model=dict, summary="刷新行情缓存")
def refresh_data(
    symbols: str = Query(..., description="逗号分隔"),
    years: int = Query(default=5, ge=1, le=20),
    ctx: AppContext = Depends(get_ctx),
    _: User = Depends(get_current_user),
) -> dict:
    from ...core.markets import parse_instrument

    codes = []
    for raw in symbols.split(","):
        text = raw.strip()
        if not text:
            continue
        try:
            codes.append(parse_instrument(text).id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    end = date.today()
    start = end - timedelta(days=365 * years)
    return {"refreshed": ctx.data.store.refresh_many(codes[:20], start, end)}


__all__ = ["router"]
