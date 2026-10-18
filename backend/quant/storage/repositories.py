"""仓储层：把 ORM 细节收在这里，业务代码只调方法。

几个约定：

* **审计写入串行化**：哈希链要求"上一条的 hash"严格有序，因此 :class:`AuditRepository`
  内部加锁；SQLite 本身也只允许单写者，天然契合。
* **凭据永不回传明文**：``list``/``get`` 只返回元信息，取明文必须显式调用
  :meth:`CredentialRepository.reveal`，并且应当伴随审计记录。
* **RunRecorder 自带会话**：回测/纸盘在后台线程里跑，不能复用请求级 Session。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.orm import Session

from ..core.types import AccountSnapshot, Fill, Order
from ..security.audit import GENESIS, AuditChain, AuditEntry, verify_chain
from ..security.crypto import SecretBox, field_box
from .db import Database
from .models import (
    AuditLog,
    Base,
    Credential,
    RefreshToken,
    Run,
    User,
    UserCustomStrategy,
    utcnow_naive,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(User.username == username))

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(User)) or 0)

    def create(
        self,
        username: str,
        password_hash: str,
        *,
        role: str = "user",
        is_active: bool = True,
    ) -> User:
        user = User(username=username.strip(), password_hash=password_hash, role=role, is_active=is_active)
        self.session.add(user)
        self.session.flush()
        return user

    def list_all(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.id)))

    def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.updated_at = utcnow_naive()

    def register_success(self, user: User) -> None:
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = utcnow_naive()

    def register_failure(self, user: User, *, threshold: int = 5, lock_minutes: int = 15) -> None:
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= threshold:
            user.locked_until = utcnow_naive() + timedelta(minutes=lock_minutes)
            log.warning("账户 %s 连续失败 %d 次，锁定至 %s", user.username, user.failed_attempts, user.locked_until)

    def is_locked(self, user: User) -> bool:
        return bool(user.locked_until and user.locked_until > utcnow_naive())


class TokenRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        user_id: int,
        jti: str,
        token_hash: str,
        expires_at: datetime,
        *,
        user_agent: str = "",
        ip: str = "",
    ) -> RefreshToken:
        record = RefreshToken(
            user_id=user_id,
            jti=jti,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent[:255],
            ip=ip[:64],
        )
        self.session.add(record)
        self.session.flush()
        return record

    def find(self, jti: str) -> RefreshToken | None:
        return self.session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))

    def revoke(self, record: RefreshToken) -> None:
        record.revoked_at = utcnow_naive()

    def revoke_all(self, user_id: int) -> int:
        result = self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow_naive())
        )
        return int(result.rowcount or 0)

    def revoke_jti(self, jti: str) -> bool:
        record = self.find(jti)
        if record is None or record.revoked_at is not None:
            return False
        self.revoke(record)
        return True

    def purge_expired(self, *, days: int = 30) -> int:
        cutoff = utcnow_naive() - timedelta(days=days)
        result = self.session.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
        return int(result.rowcount or 0)

    def active_sessions(self, user_id: int) -> list[RefreshToken]:
        return list(
            self.session.scalars(
                select(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > utcnow_naive(),
                )
                .order_by(desc(RefreshToken.created_at))
            )
        )


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# 加密凭据
# --------------------------------------------------------------------------- #


class CredentialRepository:
    def __init__(self, session: Session, box: SecretBox | None = None) -> None:
        self.session = session
        self.box = box or field_box()

    def create(
        self,
        owner_id: int,
        name: str,
        kind: str,
        payload: dict[str, Any],
        description: str = "",
    ) -> Credential:
        record = Credential(
            owner_id=owner_id,
            name=name,
            kind=kind,
            description=description,
            payload=self.box.encrypt_json(payload, aad=f"credential:{owner_id}:{name}"),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def update(self, record: Credential, payload: dict[str, Any]) -> Credential:
        record.payload = self.box.encrypt_json(
            payload, aad=f"credential:{record.owner_id}:{record.name}"
        )
        record.updated_at = utcnow_naive()
        return record

    def get(self, credential_id: int, *, owner_id: int | None = None) -> Credential | None:
        record = self.session.get(Credential, credential_id)
        if record is None:
            return None
        if owner_id is not None and record.owner_id != owner_id:
            return None
        return record

    def list_for(self, owner_id: int) -> list[Credential]:
        return list(self.session.scalars(select(Credential).where(Credential.owner_id == owner_id)))

    def reveal(self, record: Credential) -> dict[str, Any]:
        """解密并返回明文。调用方应写入审计日志。"""
        return self.box.decrypt_json(record.payload, aad=f"credential:{record.owner_id}:{record.name}")

    def delete(self, record: Credential) -> None:
        self.session.delete(record)


# --------------------------------------------------------------------------- #
# 用户自建策略（条件拼装）
# --------------------------------------------------------------------------- #


class UserStrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        owner_id: int,
        *,
        name: str,
        spec: dict[str, Any],
        description: str = "",
        category: str = "trend",
    ) -> UserCustomStrategy:
        record = UserCustomStrategy(
            user_id=owner_id,
            name=name,
            description=description,
            category=category,
            spec=spec,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def update(
        self,
        record: UserCustomStrategy,
        *,
        name: str | None = None,
        spec: dict[str, Any] | None = None,
        description: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
    ) -> UserCustomStrategy:
        if name is not None:
            record.name = name
        if spec is not None:
            record.spec = spec
        if description is not None:
            record.description = description
        if category is not None:
            record.category = category
        if enabled is not None:
            record.enabled = enabled
        record.updated_at = utcnow_naive()
        self.session.flush()
        return record

    def get(self, strategy_id: int, *, owner_id: int) -> UserCustomStrategy | None:
        """仅限本人可见——拼装策略不做跨用户共享。"""
        record = self.session.get(UserCustomStrategy, strategy_id)
        if record is None or record.user_id != owner_id:
            return None
        return record

    def list_for(self, owner_id: int) -> list[UserCustomStrategy]:
        return list(
            self.session.scalars(
                select(UserCustomStrategy)
                .where(UserCustomStrategy.user_id == owner_id)
                .order_by(desc(UserCustomStrategy.updated_at))
            )
        )

    def delete(self, record: UserCustomStrategy) -> None:
        self.session.delete(record)


# --------------------------------------------------------------------------- #
# 运行记录
# --------------------------------------------------------------------------- #


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        run_id: str,
        *,
        user_id: int | None,
        kind: str,
        strategy_id: str,
        symbols: Sequence[str],
        config: dict[str, Any],
        status: str = "running",
    ) -> Run:
        record = Run(
            id=run_id,
            user_id=user_id,
            kind=kind,
            strategy_id=strategy_id,
            symbols=list(symbols),
            config=config,
            status=status,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, run_id: str) -> Run | None:
        return self.session.get(Run, run_id)

    def list(
        self,
        *,
        user_id: int | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        stmt = select(Run).order_by(desc(Run.created_at)).limit(limit).offset(offset)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        if kind:
            stmt = stmt.where(Run.kind == kind)
        return list(self.session.scalars(stmt))

    def count(self, *, user_id: int | None = None) -> int:
        stmt = select(func.count()).select_from(Run)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        return int(self.session.scalar(stmt) or 0)

    def delete(self, run_id: str) -> bool:
        record = self.get(run_id)
        if record is None:
            return False
        self.session.delete(record)
        return True

    def mark_interrupted(self) -> int:
        """服务重启时，把残留的 running 状态标记为失败，避免前端永远转圈。"""
        result = self.session.execute(
            update(Run).where(Run.status == "running").values(status="interrupted", error="服务重启导致运行中断")
        )
        return int(result.rowcount or 0)


class RunRecorder:
    """把引擎事件写进 ``runs`` 表（实现 :class:`~quant.engine.session.RunRecorderProtocol`）。

    所有写操作都用**独立的短事务**，避免长时间占用写锁影响 API。
    内存里同时保留一份，``finalize`` 时一次性落库压缩 JSON。
    """

    def __init__(
        self,
        db: Database,
        run_id: str,
        *,
        user_id: int | None = None,
        kind: str = "backtest",
        strategy_id: str = "",
        symbols: Sequence[str] = (),
        config: dict[str, Any] | None = None,
        flush_every: int = 500,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.user_id = user_id
        self.kind = kind
        self.strategy_id = strategy_id
        self.symbols = list(symbols)
        self.config = config or {}
        self.flush_every = max(1, flush_every)

        self.equity: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.logs: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_flush = 0
        self._started = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            with self.db.session() as session:
                RunRepository(session).create(
                    self.run_id,
                    user_id=self.user_id,
                    kind=self.kind,
                    strategy_id=self.strategy_id,
                    symbols=self.symbols,
                    config=self.config,
                    status="running",
                )
            self._started = True

    # -- 引擎协议 ---------------------------------------------------------- #
    def record_equity(self, snapshot: AccountSnapshot | dict[str, Any]) -> None:
        data = snapshot.to_dict() if isinstance(snapshot, AccountSnapshot) else dict(snapshot)
        with self._lock:
            self.equity.append(data)
        self._maybe_flush()

    def record_fill(self, fill: Fill | dict[str, Any]) -> None:
        data = fill.to_dict() if isinstance(fill, Fill) else dict(fill)
        with self._lock:
            self.fills.append(data)

    # 引擎通过事件总线传的是 dict
    def record_fill_event(self, payload: dict[str, Any]) -> None:
        self.record_fill(payload)

    def record_order(self, order: Order | dict[str, Any]) -> None:
        data = order.to_dict() if isinstance(order, Order) else dict(order)
        with self._lock:
            self.orders[data["id"]] = data

    def record_order_event(self, payload: dict[str, Any]) -> None:
        self.record_order(payload)

    def record_log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append({"ts": datetime.utcnow().isoformat(), "message": message, "level": level})
            if len(self.logs) > 1000:
                del self.logs[: len(self.logs) - 1000]

    def record_log_event(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.logs.append(dict(payload))
            if len(self.logs) > 1000:
                del self.logs[: len(self.logs) - 1000]

    # ------------------------------------------------------------------ #
    def update_progress(self, progress: float, message: str = "") -> None:
        with self._lock:
            should = int(progress * 100) % 5 == 0
        if not should:
            return
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.progress = float(progress)
                    if message and len(self.logs) < 1000:
                        self.record_log(message, "progress")
        except Exception:  # noqa: BLE001 - 进度写失败不应中断回测
            log.debug("进度更新失败", exc_info=True)

    def touch(self) -> None:
        """纸盘定期心跳，让前端知道还活着。"""
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.progress = 1.0
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    def _maybe_flush(self) -> None:
        if len(self.equity) - self._last_flush < self.flush_every:
            return
        self._last_flush = len(self.equity)
        try:
            with self.db.session() as session:
                record = RunRepository(session).get(self.run_id)
                if record is not None:
                    record.equity_curve = self.equity[-2000:]
                    record.trades = self.fills[-500:]
                    record.orders_data = list(self.orders.values())[-500:]
        except Exception:  # noqa: BLE001
            log.debug("中间结果落库失败", exc_info=True)

    # ------------------------------------------------------------------ #
    def finalize(self, result: Any) -> None:
        """把最终结果写入 runs 表。``result`` 可以是 EngineResult 或其 dict。"""
        payload = result.to_dict(max_points=5000) if hasattr(result, "to_dict") else dict(result)
        with self._lock:
            equity = payload.get("equity_curve") or self.equity
            trades = payload.get("trades") or self.fills
            orders = payload.get("orders") or list(self.orders.values())
            positions = payload.get("positions") or []
            logs = payload.get("logs") or self.logs
            metrics = payload.get("metrics") or {}
            status = payload.get("status", "finished")
            error = payload.get("error")
            elapsed = float(payload.get("elapsed_seconds") or 0.0)

        try:
            with self.db.session() as session:
                repo = RunRepository(session)
                record = repo.get(self.run_id)
                if record is None:
                    record = repo.create(
                        self.run_id,
                        user_id=self.user_id,
                        kind=self.kind,
                        strategy_id=self.strategy_id,
                        symbols=self.symbols,
                        config=self.config,
                        status=status,
                    )
                record.status = status
                record.metrics = metrics
                record.equity_curve = equity
                record.trades = trades
                record.orders_data = orders
                record.positions = positions
                record.logs = logs[-1000:]
                record.error = error
                record.progress = 1.0 if status in ("finished", "failed", "cancelled") else record.progress
                record.elapsed_seconds = elapsed
                record.finished_at = utcnow_naive()
        except Exception:  # noqa: BLE001
            log.exception("运行结果落库失败 run_id=%s", self.run_id)


# --------------------------------------------------------------------------- #
# 初始化
# --------------------------------------------------------------------------- #


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


__all__ = [
    "AuditRepository",
    "CredentialRepository",
    "RunRecorder",
    "RunRepository",
    "TokenRepository",
    "UserRepository",
    "UserStrategyRepository",
    "bootstrap",
]
