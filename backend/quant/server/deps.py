"""FastAPI 依赖：数据库会话、当前用户、管理员校验、客户端信息。"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..core.errors import AuthError, PermissionDenied
from ..security.tokens import TokenService
from ..storage.models import User
from ..storage.repositories import AuditRepository, UserRepository
from .context import AppContext

log = logging.getLogger(__name__)


def get_ctx(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:  # pragma: no cover - 正常启动不会发生
        raise RuntimeError("应用上下文未初始化")
    return ctx


def get_db(ctx: AppContext = Depends(get_ctx)) -> Iterator[Session]:
    with ctx.db.session() as session:
        yield session


def client_ip(request: Request) -> str:
    """优先取反向代理透传的真实 IP（Caddy/Nginx 已配置可信头）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def bearer_token(request: Request) -> str:
    value = request.headers.get("authorization")
    token = TokenService.extract_bearer(value)
    if not token:
        raise AuthError("未提供认证令牌")
    return token


def get_current_user(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    session: Session = Depends(get_db),
    token: str = Depends(bearer_token),
) -> User:
    claims = ctx.token_service.decode(token, expected_type="access")
    user = UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthError("用户不存在或已停用")
    request.state.user = user
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise PermissionDenied("需要管理员权限")
    return user


def audit(
    session: Session,
    request: Request | None,
    *,
    actor: str,
    action: str,
    target: str = "",
    detail: dict | None = None,
) -> None:
    """写一条审计记录（与调用方同事务，保证业务成功才留痕）。"""
    AuditRepository(session).append(
        actor=actor,
        action=action,
        target=target,
        detail=detail or {},
        ip=client_ip(request) if request is not None else "",
    )


__all__ = [
    "audit",
    "bearer_token",
    "client_ip",
    "get_ctx",
    "get_current_user",
    "get_db",
    "require_admin",
]
