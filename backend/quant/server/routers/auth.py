"""认证与账户安全路由。

``/api/auth/*``

安全细节：

* 登录失败计数落库（**先 commit 再抛错**，否则依赖回滚会把计数抹掉）。
* 用户不存在时也执行一次哈希校验，避免通过响应时间枚举账号。
* 刷新令牌一次性轮换；改密/禁用 TOTP 后吊销全部会话。
* 所有敏感操作写审计链。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...core.errors import AuthError, ConflictError, InvalidRequest, PermissionDenied
from ...security.password import WeakPassword, hash_password, needs_rehash, validate_strength, verify_password
from ...services import captcha_service
from ...storage.models import RefreshToken, User
from ...storage.repositories import TokenRepository, UserRepository
from ..context import AppContext
from ..deps import audit, client_ip, get_ctx, get_current_user, get_db
from ..schemas import (
    CaptchaOut,
    ChangePasswordRequest,
    LoginRequest,
    Message,
    RefreshRequest,
    RegisterRequest,
    SessionOut,
    TokenResponse,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpSetupResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["认证"])
log = logging.getLogger(__name__)

# 用户不存在时用来"陪跑"的哈希，避免时序侧信道
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _user_out(user: User) -> UserOut:
    return UserOut(**user.to_dict())


def _fail(session: Session, message: str, status: int = 401) -> None:
    """先提交副作用（失败计数/审计）再抛错。"""
    session.commit()
    raise AuthError(message)


# --------------------------------------------------------------------------- #
# 登录 / 刷新 / 登出
# --------------------------------------------------------------------------- #


@router.get("/captcha", response_model=CaptchaOut, summary="获取注册用图形验证码")
def captcha(ctx: AppContext = Depends(get_ctx)) -> CaptchaOut:
    challenge = captcha_service.issue(ctx.master.raw, ttl=captcha_service.TTL_SECONDS)
    return CaptchaOut(token=challenge.token, image=challenge.image, expires_in=captcha_service.TTL_SECONDS)


@router.post("/register", response_model=Message, status_code=201, summary="注册账号（需管理员审核）")
def register(
    body: RegisterRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    session: Session = Depends(get_db),
):
    if not captcha_service.verify(ctx.master.raw, body.captcha_token, body.captcha_answer):
        raise InvalidRequest("人机验证失败或已过期，请刷新验证码重试")

    try:
        validate_strength(body.password)
    except WeakPassword as exc:
        raise InvalidRequest(f"密码强度不足：{exc}") from exc

    users = UserRepository(session)
    username = body.username.strip()
    if users.get_by_username(username):
        raise ConflictError("用户名已存在")

    user = users.create(
        username,
        hash_password(body.password),
        role="user",
        is_active=False,
        approval_status="pending",
    )
    session.flush()
    audit(
        session,
        request,
        actor=username,
        action="auth.register",
        target=f"user:{user.id}",
        detail={"status": "pending"},
    )
    return Message(message="注册申请已提交，等待管理员审核")


@router.post("/login", response_model=TokenResponse, summary="账号密码登录（可带 TOTP）")
def login(
    body: LoginRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    session: Session = Depends(get_db),
):
    users = UserRepository(session)
    tokens = TokenRepository(session)
    user = users.get_by_username(body.username)

    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        audit(
            session,
            request,
            actor=body.username,
            action="auth.login.failed",
            detail={"reason": "user_not_found"},
        )
        _fail(session, "用户名或密码错误")

    if user.approval_status == "pending":
        audit(
            session,
            request,
            actor=user.username,
            action="auth.login.failed",
            detail={"reason": "pending_approval"},
        )
        session.commit()
        raise PermissionDenied("账号正在等待管理员审核，请稍后再试")

    if user.approval_status == "rejected":
        audit(
            session,
            request,
            actor=user.username,
            action="auth.login.failed",
            detail={"reason": "rejected"},
        )
        session.commit()
        raise PermissionDenied("注册申请未通过审核，请联系管理员")

    if not user.is_active:
        audit(
            session, request, actor=user.username, action="auth.login.failed", detail={"reason": "inactive"}
        )
        _fail(session, "账号已停用，请联系管理员", 403)

    if users.is_locked(user):
        audit(session, request, actor=user.username, action="auth.login.failed", detail={"reason": "locked"})
        _fail(session, "账号因多次登录失败被临时锁定，请稍后再试", 423)

    if not verify_password(body.password, user.password_hash):
        users.register_failure(
            user,
            threshold=ctx.settings.login_max_attempts,
            lock_minutes=ctx.settings.login_lock_minutes,
        )
        audit(
            session,
            request,
            actor=user.username,
            action="auth.login.failed",
            detail={"reason": "bad_password"},
        )
        _fail(session, "用户名或密码错误")

    # 密码算法升级（scrypt → Argon2id，或参数过期）
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    # 两步验证
    if user.totp_enabled:
        if not body.totp_code:
            users.register_success(user)
            session.commit()
            raise AuthError("需要两步验证码")
        secret = ctx.totp_service.decrypt_secret(user.totp_secret or "", user_id=user.id)
        if not ctx.totp_service.verify(secret, body.totp_code):
            used_recovery = _consume_recovery(user, body.totp_code)
            if not used_recovery:
                users.register_failure(user, threshold=ctx.settings.login_max_attempts)
                audit(
                    session,
                    request,
                    actor=user.username,
                    action="auth.login.failed",
                    detail={"reason": "bad_totp"},
                )
                _fail(session, "两步验证码不正确")

    users.register_success(user)
    pair = ctx.token_service.issue_pair(user.id, user.role)
    claims = ctx.token_service.decode(pair.refresh_token)
    tokens.create(
        user.id,
        claims.jti,
        ctx.token_service.hash_token(pair.refresh_token),
        claims.expires_at.replace(tzinfo=None),
        user_agent=request.headers.get("user-agent", ""),
        ip=client_ip(request),
    )
    audit(session, request, actor=user.username, action="auth.login", target=f"user:{user.id}")

    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        refresh_expires_in=pair.refresh_expires_in,
        user=_user_out(user),
    )


def _consume_recovery(user: User, code: str) -> bool:
    """校验并消费一枚恢复码。"""
    if not user.totp_recovery or "-" not in code:
        return False
    from ...security.totp import TotpService

    hashes = list(user.totp_recovery)
    if not TotpService.verify_recovery_code(code, hashes):
        return False
    normalized = TotpService.hash_recovery_code(code)
    user.totp_recovery = [h for h in hashes if h != normalized]
    return True


@router.post("/refresh", response_model=TokenResponse, summary="刷新令牌（一次性轮换）")
def refresh(
    body: RefreshRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    session: Session = Depends(get_db),
):
    claims = ctx.token_service.decode(body.refresh_token, expected_type="refresh")
    tokens = TokenRepository(session)
    record = tokens.find(claims.jti)
    if record is None or not record.is_valid:
        audit(session, request, actor=claims.sub, action="auth.refresh.rejected", detail={"jti": claims.jti})
        _fail(session, "刷新令牌已失效，请重新登录")

    if record.token_hash != ctx.token_service.hash_token(body.refresh_token):
        tokens.revoke(record)
        audit(session, request, actor=claims.sub, action="auth.refresh.reuse", detail={"jti": claims.jti})
        _fail(session, "令牌校验失败")

    users = UserRepository(session)
    user = users.get(record.user_id)
    if user is None or not user.is_active:
        _fail(session, "用户不存在或已停用", 403)

    # 轮换：旧令牌立即作废
    tokens.revoke(record)
    pair = ctx.token_service.issue_pair(user.id, user.role)
    new_claims = ctx.token_service.decode(pair.refresh_token)
    tokens.create(
        user.id,
        new_claims.jti,
        ctx.token_service.hash_token(pair.refresh_token),
        new_claims.expires_at.replace(tzinfo=None),
        user_agent=request.headers.get("user-agent", ""),
        ip=client_ip(request),
    )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        refresh_expires_in=pair.refresh_expires_in,
        user=_user_out(user),
    )


@router.post("/logout", response_model=Message, summary="登出（吊销当前 refresh）")
def logout(
    body: RefreshRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    try:
        claims = ctx.token_service.decode(body.refresh_token, expected_type="refresh")
    except AuthError:
        return Message(message="已登出")
    TokenRepository(session).revoke_jti(claims.jti)
    audit(session, request, actor=user.username, action="auth.logout", target=f"jti:{claims.jti}")
    return Message(message="已登出")


@router.get("/me", response_model=UserOut, summary="当前用户")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


# --------------------------------------------------------------------------- #
# 密码
# --------------------------------------------------------------------------- #


@router.post("/password", response_model=Message, summary="修改密码")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not verify_password(body.old_password, user.password_hash):
        audit(session, request, actor=user.username, action="auth.password.failed")
        _fail(session, "原密码不正确", 400)
    try:
        validate_strength(body.new_password)
    except WeakPassword as exc:
        raise WeakPassword(str(exc)) from exc

    users = UserRepository(session)
    users.update_password(user, hash_password(body.new_password))
    revoked = TokenRepository(session).revoke_all(user.id)
    audit(
        session,
        request,
        actor=user.username,
        action="auth.password.changed",
        target=f"user:{user.id}",
        detail={"revoked_sessions": revoked},
    )
    return Message(message="密码已修改，其它设备的登录状态已失效", detail={"revoked_sessions": revoked})


# --------------------------------------------------------------------------- #
# 两步验证
# --------------------------------------------------------------------------- #


@router.post("/totp/setup", response_model=TotpSetupResponse, summary="生成 TOTP 种子（尚未启用）")
def totp_setup(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if user.totp_enabled:
        raise PermissionDenied("两步验证已启用；如需重置请先关闭")

    secret = ctx.totp_service.generate_secret()
    user.totp_secret = ctx.totp_service.encrypt_secret(secret, user_id=user.id)
    codes = ctx.totp_service.generate_recovery_codes()
    from ...security.totp import TotpService

    user.totp_recovery = [TotpService.hash_recovery_code(c) for c in codes]
    user.totp_enabled = False
    audit(session, request, actor=user.username, action="auth.totp.setup")
    return TotpSetupResponse(
        secret=secret,
        provisioning_uri=ctx.totp_service.provisioning_uri(secret, user.username),
        recovery_codes=codes,
        issuer=ctx.totp_service.issuer,
    )


@router.post("/totp/enable", response_model=Message, summary="校验并启用两步验证")
def totp_enable(
    body: TotpEnableRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if user.totp_enabled:
        raise PermissionDenied("两步验证已启用")
    if not user.totp_secret:
        raise AuthError("请先调用 /auth/totp/setup 生成种子")
    secret = ctx.totp_service.decrypt_secret(user.totp_secret, user_id=user.id)
    if not ctx.totp_service.verify(secret, body.code):
        raise AuthError("验证码不正确")
    user.totp_enabled = True
    user.totp_confirmed_at = datetime.utcnow()
    audit(session, request, actor=user.username, action="auth.totp.enabled")
    return Message(message="两步验证已启用")


@router.post("/totp/disable", response_model=Message, summary="关闭两步验证")
def totp_disable(
    body: TotpDisableRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not user.totp_enabled:
        raise PermissionDenied("两步验证未启用")
    if not verify_password(body.password, user.password_hash):
        audit(session, request, actor=user.username, action="auth.totp.disable.failed")
        _fail(session, "密码不正确", 400)
    secret = ctx.totp_service.decrypt_secret(user.totp_secret or "", user_id=user.id)
    if not ctx.totp_service.verify(secret, body.code):
        raise AuthError("验证码不正确")

    user.totp_enabled = False
    user.totp_secret = None
    user.totp_recovery = None
    user.totp_confirmed_at = None
    audit(session, request, actor=user.username, action="auth.totp.disabled")
    return Message(message="两步验证已关闭")


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


@router.get("/sessions", response_model=list[SessionOut], summary="当前有效会话")
def list_sessions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    records = TokenRepository(session).active_sessions(user.id)
    return [
        SessionOut(
            id=r.id,
            jti=r.jti,
            ip=r.ip,
            user_agent=r.user_agent,
            created_at=r.created_at.isoformat(),
            expires_at=r.expires_at.isoformat(),
        )
        for r in records
    ]


@router.delete("/sessions/{session_id}", response_model=Message, summary="吊销指定会话")
def revoke_session(
    session_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record: RefreshToken | None = session.get(RefreshToken, session_id)
    if record is None or record.user_id != user.id:
        raise PermissionDenied("会话不存在")
    TokenRepository(session).revoke(record)
    audit(
        session, request, actor=user.username, action="auth.session.revoked", target=f"session:{session_id}"
    )
    return Message(message="会话已吊销")


__all__ = ["router"]
