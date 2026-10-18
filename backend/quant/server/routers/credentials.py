"""加密凭据路由。

**明文只在写入的一瞬间存在于内存里**：入库前用 AES-256-GCM 加密，
之后任何接口都不会返回明文（``reveal`` 仅限管理员且写审计）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.errors import CryptoError
from ...storage.models import User
from ...storage.repositories import CredentialRepository
from ..context import AppContext
from ..deps import audit, get_current_user, get_ctx, get_db
from ..schemas import CredentialCreate, CredentialOut, Message

router = APIRouter(prefix="/credentials", tags=["凭据"])
log = logging.getLogger(__name__)


@router.get("", response_model=list[CredentialOut], summary="凭据列表（不含密文）")
def list_credentials(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    records = CredentialRepository(session).list_for(user.id)
    return [CredentialOut(**r.to_dict()) for r in records]


@router.post("", response_model=CredentialOut, status_code=201, summary="保存加密凭据")
def create_credential(
    body: CredentialCreate,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = CredentialRepository(session)
    record = repo.create(user.id, body.name, body.kind, body.payload, body.description)
    audit(
        session,
        request=None,
        actor=user.username,
        action="credential.create",
        target=f"credential:{record.id}",
        detail={"name": body.name, "kind": body.kind, "fields": sorted(body.payload)},
    )
    return CredentialOut(**record.to_dict())


@router.put("/{credential_id}", response_model=CredentialOut, summary="更新凭据内容")
def update_credential(
    credential_id: int,
    body: CredentialCreate,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = CredentialRepository(session)
    record = repo.get(credential_id, owner_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    repo.update(record, body.payload)
    audit(session, request=None, actor=user.username, action="credential.update", target=f"credential:{record.id}")
    return CredentialOut(**record.to_dict())


@router.post("/{credential_id}/reveal", response_model=dict, summary="解密查看（仅管理员，写审计）")
def reveal_credential(
    credential_id: int,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看明文凭据")
    repo = CredentialRepository(session)
    record = repo.get(credential_id)
    if record is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    try:
        payload = repo.reveal(record)
    except CryptoError as exc:
        raise HTTPException(status_code=500, detail=f"解密失败：{exc}") from exc
    audit(
        session,
        request=None,
        actor=user.username,
        action="credential.reveal",
        target=f"credential:{record.id}",
        detail={"name": record.name},
    )
    return {"id": record.id, "name": record.name, "payload": payload}


@router.delete("/{credential_id}", response_model=Message, summary="删除凭据")
def delete_credential(
    credential_id: int,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    repo = CredentialRepository(session)
    record = repo.get(credential_id, owner_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    repo.delete(record)
    audit(session, request=None, actor=user.username, action="credential.delete", target=f"credential:{credential_id}")
    return Message(message="已删除")


__all__ = ["router"]
