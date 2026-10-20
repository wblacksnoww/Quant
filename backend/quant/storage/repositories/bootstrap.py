"""首次启动时创建管理员（幂等）。"""

from __future__ import annotations

import logging

from ..db import Database
from ..models import User
from .audit import AuditRepository
from .users import UserRepository

log = logging.getLogger(__name__)


def bootstrap(db: Database, *, username: str, password_hash: str) -> User | None:
    """首次启动时创建管理员（幂等）。"""
    with db.session() as session:
        repo = UserRepository(session)
        if repo.count() > 0:
            return None
        user = repo.create(username, password_hash, role="admin")
        AuditRepository(session).append(
            actor=username, action="system.bootstrap", target=f"user:{user.id}", detail={"role": "admin"}
        )
        log.warning("已创建初始管理员账号 %s，请立即登录并修改密码、开启两步验证", username)
        return user
