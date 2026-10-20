"""防篡改审计日志：哈希链。

每条记录的哈希 = SHA-256(上一条哈希 + 规范化 JSON(本条内容))。

于是：

* 改动任何一条历史记录 → 它之后的所有哈希都对不上，:func:`verify_chain` 立刻发现。
* 删除中间一条 → 链断。
* 攻击者若想伪造，必须重算整条链并覆盖全部历史——在只追加的表上做不到。

这不是"防篡改的银弹"（拥有数据库写权限的人仍可重算整条链），
但足以让**悄悄改账**变得困难，并给合规审计留下可信证据链。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

GENESIS = "0" * 64


def canonical(payload: Mapping[str, Any]) -> str:
    """规范 JSON：键排序、无空格、保留中文，保证同一内容哈希稳定。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class AuditEntry:
    ts: str
    actor: str
    action: str
    target: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    ip: str = ""
    prev_hash: str = GENESIS
    hash: str = ""

    def body(self) -> dict[str, Any]:
        """参与哈希的内容（不含 prev_hash/hash 自身）。"""
        return {
            "ts": self.ts,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "detail": self.detail,
            "ip": self.ip,
        }

    def compute_hash(self, prev_hash: str) -> str:
        return chain_hash(prev_hash, self.body())

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "prev_hash": self.prev_hash, "hash": self.hash}


def chain_hash(prev_hash: str, body: Mapping[str, Any]) -> str:
    return hashlib.sha256(f"{prev_hash}|{canonical(body)}".encode()).hexdigest()


class AuditChain:
    """内存中的链式构造器；持久化由存储层负责。

    用法::

        chain = AuditChain(last_hash=db.last_hash())
        entry = chain.append(actor="admin", action="login")
        db.insert(entry)
    """

    def __init__(self, last_hash: str = GENESIS) -> None:
        self.last_hash = last_hash or GENESIS
        self.length = 0

    def append(
        self,
        *,
        actor: str,
        action: str,
        target: str = "",
        detail: Mapping[str, Any] | None = None,
        ip: str = "",
        ts: datetime | None = None,
    ) -> AuditEntry:
        timestamp = (ts or datetime.utcnow()).isoformat(timespec="milliseconds")
        draft = AuditEntry(
            ts=timestamp,
            actor=actor,
            action=action,
            target=target,
            detail=dict(detail or {}),
            ip=ip,
            prev_hash=self.last_hash,
        )
        digest = draft.compute_hash(self.last_hash)
        entry = AuditEntry(
            ts=draft.ts,
            actor=draft.actor,
            action=draft.action,
            target=draft.target,
            detail=draft.detail,
            ip=draft.ip,
            prev_hash=self.last_hash,
            hash=digest,
        )
        self.last_hash = digest
        self.length += 1
        return entry


def verify_chain(entries: Iterable[AuditEntry]) -> tuple[bool, int, str]:
    """校验整条链。返回 ``(是否完整, 已校验条数, 出错说明)``。"""
    expected_prev = GENESIS
    count = 0
    for entry in entries:
        count += 1
        if entry.prev_hash != expected_prev:
            return False, count, f"第 {count} 条的 prev_hash 与上一条不符（链断裂）"
        if entry.compute_hash(entry.prev_hash) != entry.hash:
            return False, count, f"第 {count} 条内容与哈希不匹配（疑似被篡改）"
        expected_prev = entry.hash
    return True, count, ""


__all__ = ["GENESIS", "AuditChain", "AuditEntry", "canonical", "chain_hash", "verify_chain"]
