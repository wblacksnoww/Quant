"""哈希链审计日志仓储。"""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ...security.audit import GENESIS, AuditChain, AuditEntry, verify_chain
from ..models import AuditLog


class AuditRepository:
    """哈希链审计日志。写入串行化，校验全链。"""

    _lock = threading.Lock()

    def __init__(self, session: Session) -> None:
        self.session = session

    def last_hash(self) -> str:
        value = self.session.scalar(select(AuditLog.hash).order_by(desc(AuditLog.id)).limit(1))
        return value or GENESIS

    def append(
        self,
        *,
        actor: str,
        action: str,
        target: str = "",
        detail: dict[str, Any] | None = None,
        ip: str = "",
    ) -> AuditEntry:
        with self._lock:
            chain = AuditChain(self.last_hash())
            entry = chain.append(actor=actor, action=action, target=target, detail=detail, ip=ip)
            self.session.add(
                AuditLog(
                    ts=entry.ts,
                    actor=entry.actor,
                    action=entry.action,
                    target=entry.target,
                    detail=entry.detail,
                    ip=entry.ip,
                    prev_hash=entry.prev_hash,
                    hash=entry.hash,
                )
            )
            self.session.flush()
            return entry

    def list(self, *, limit: int = 100, offset: int = 0, action: str | None = None) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(desc(AuditLog.id)).limit(limit).offset(offset)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list(self.session.scalars(stmt))

    def verify(self) -> tuple[bool, int, str]:
        rows = list(self.session.scalars(select(AuditLog).order_by(AuditLog.id)))
        entries = [
            AuditEntry(
                ts=r.ts,
                actor=r.actor,
                action=r.action,
                target=r.target,
                detail=r.detail or {},
                ip=r.ip,
                prev_hash=r.prev_hash,
                hash=r.hash,
            )
            for r in rows
        ]
        return verify_chain(entries)

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(AuditLog)) or 0)
