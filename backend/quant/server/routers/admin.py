"""管理员路由：用户管理。

只有 admin 可见。创建用户时强制口令强度校验；停用用户会同时吊销其全部会话。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...security.password import WeakPassword, hash_password, validate_strength
from ...storage.models import User
from ...storage.repositories import TokenRepository, UserRepository
from ..deps import audit, get_db, require_admin
from ..schemas import Message, UserOut

router = APIRouter(prefix="/admin", tags=["管理"])
log = logging.getLogger(__name__)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    role: str = Field(default="user", pattern="^(user|admin)$")


class UserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, pattern="^(user|admin)$")
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)


@router.get("/users", response_model=list[UserOut], summary="用户列表")
def list_users(
    _: User = Depends(require_admin),
    session: Session = Depends(get_db),
):
    return [UserOut(**u.to_dict()) for u in UserRepository(session).list_all()]


@router.post("/users", response_model=UserOut, status_code=201, summary="创建用户")
def create_user(
    body: UserCreateRequest,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
):
    try:
        validate_strength(body.password)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = UserRepository(session)
    if repo.get_by_username(body.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = repo.create(body.username, hash_password(body.password), role=body.role)
    audit(
        session,
        request=None,
        actor=admin.username,
        action="admin.user.create",
        target=f"user:{user.id}",
        detail={"username": user.username, "role": user.role},
    )
    return UserOut(**user.to_dict())


@router.patch("/users/{user_id}", response_model=UserOut, summary="修改角色/启停")
def update_user(
    user_id: int,
    body: UserUpdateRequest,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
):
    repo = UserRepository(session)
    user = repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="不能停用自己")

    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            TokenRepository(session).revoke_all(user.id)
    audit(
        session,
        request=None,
        actor=admin.username,
        action="admin.user.update",
        target=f"user:{user.id}",
        detail=body.model_dump(exclude_none=True),
    )
    return UserOut(**user.to_dict())


@router.post("/users/{user_id}/reset-password", response_model=Message, summary="重置密码")
def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
):
    try:
        validate_strength(body.new_password)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = UserRepository(session)
    user = repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    repo.update_password(user, hash_password(body.new_password))
    revoked = TokenRepository(session).revoke_all(user.id)
    audit(
        session,
        request=None,
        actor=admin.username,
        action="admin.user.reset_password",
        target=f"user:{user.id}",
        detail={"revoked_sessions": revoked},
    )
    return Message(message="密码已重置，该用户需要重新登录")


__all__ = ["router"]
