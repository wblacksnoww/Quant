"""跨实例会话协调：数据库租约 + 控制命令 + 状态快照。

设计目标：多 worker / 多副本部署时，同一个纸盘或实盘会话只能由一个实例的
引擎线程持有。任何实例都可以：

* 通过租约原子抢占会话（``acquire``）；
* 读取租约里的状态快照来响应 ``/sessions`` / ``/state``；
* 写入 stop/pause/resume/switch 命令，由持有租约的实例轮询执行。

单实例部署行为不变：本地引擎存在时命令立即执行，不经过轮询。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..storage.db import Database
from ..storage.models import SessionCommand, SessionLease, utcnow_naive

log = logging.getLogger(__name__)

_STATE_KEYS = (
    "run_id",
    "strategy_id",
    "symbols",
    "mode",
    "adapter",
    "sandbox",
    "credential_id",
    "status",
    "started_at",
    "last_prices",
    "cash",
    "frozen_cash",
    "equity",
    "total_return",
    "positions",
    "open_orders",
    "recent_fills",
    "equity_curve",
    "error",
    "steps",
    "reconcile_at",
)


def summarize(state: dict[str, Any] | None) -> dict[str, Any]:
    """压掉大字段（权益曲线、长列表），只保留跨实例展示需要的快照。"""
    if not state:
        return {}
    out = {key: state[key] for key in _STATE_KEYS if key in state}
    out["positions"] = list(out.get("positions") or [])[:20]
    out["open_orders"] = list(out.get("open_orders") or [])[:20]
    out["recent_fills"] = list(out.get("recent_fills") or [])[-10:]
    out["equity_curve"] = []
    return out


class LeaseStore:
    """数据库租约/命令存储（SQLite 与 PostgreSQL 都可用）。"""

    def __init__(self, db: Database, instance_id: str) -> None:
        self.db = db
        self.instance_id = instance_id

    # -- 租约 ------------------------------------------------------------- #
    def acquire(
        self,
        key: str,
        *,
        kind: str,
        user_id: int,
        run_id: str,
        state: dict[str, Any] | None,
        ttl: float,
    ) -> bool:
        now = utcnow_naive()
        expires = now + timedelta(seconds=ttl)
        try:
            with self.db.session() as session:
                lease = session.get(SessionLease, key)
                if lease is None:
                    session.add(
                        SessionLease(
                            key=key,
                            kind=kind,
                            user_id=user_id,
                            run_id=run_id,
                            owner=self.instance_id,
                            status=str((state or {}).get("status") or "running"),
                            state=state,
                            updated_at=now,
                            expires_at=expires,
                        )
                    )
                    return True
                if lease.owner != self.instance_id and lease.expires_at > now:
                    return False
                lease.owner = self.instance_id
                lease.kind = kind
                lease.user_id = user_id
                lease.run_id = run_id
                lease.status = str((state or {}).get("status") or "running")
                lease.state = state
                lease.updated_at = now
                lease.expires_at = expires
                return True
        except IntegrityError:
            # 并发插入：另一个实例刚抢到
            return False

    def renew(self, key: str, *, state: dict[str, Any] | None, ttl: float, status: str) -> bool:
        now = utcnow_naive()
        with self.db.session() as session:
            result = session.execute(
                update(SessionLease)
                .where(SessionLease.key == key, SessionLease.owner == self.instance_id)
                .values(
                    state=state,
                    status=status,
                    updated_at=now,
                    expires_at=now + timedelta(seconds=ttl),
                )
            )
            return int(result.rowcount or 0) == 1

    def release(self, key: str) -> None:
        try:
            with self.db.session() as session:
                lease = session.get(SessionLease, key)
                if lease is not None and lease.owner == self.instance_id:
                    session.delete(lease)
        except Exception:  # noqa: BLE001
            log.debug("释放会话租约失败 key=%s", key, exc_info=True)

    def release_all(self) -> None:
        try:
            with self.db.session() as session:
                for lease in session.scalars(
                    select(SessionLease).where(SessionLease.owner == self.instance_id)
                ):
                    session.delete(lease)
        except Exception:  # noqa: BLE001
            log.debug("释放本实例全部租约失败", exc_info=True)

    def get(self, key: str) -> dict[str, Any] | None:
        with self.db.session() as session:
            lease = session.get(SessionLease, key)
            if lease is None:
                return None
            return {
                "key": lease.key,
                "kind": lease.kind,
                "user_id": lease.user_id,
                "run_id": lease.run_id,
                "owner": lease.owner,
                "status": lease.status,
                "state": lease.state or {},
                "expires_at": lease.expires_at.isoformat(),
            }

    def active_for_user(self, user_id: int) -> list[dict[str, Any]]:
        now = utcnow_naive()
        with self.db.session() as session:
            leases = list(
                session.scalars(
                    select(SessionLease).where(
                        SessionLease.user_id == user_id,
                        SessionLease.expires_at > now,
                    )
                )
            )
            return [
                {
                    "key": lease.key,
                    "kind": lease.kind,
                    "user_id": lease.user_id,
                    "run_id": lease.run_id,
                    "owner": lease.owner,
                    "status": lease.status,
                    "state": lease.state or {},
                }
                for lease in leases
            ]

    def active_run_ids(self) -> set[str]:
        now = utcnow_naive()
        with self.db.session() as session:
            return set(session.scalars(select(SessionLease.run_id).where(SessionLease.expires_at > now)))

    # -- 命令 ------------------------------------------------------------- #
    def enqueue(self, key: str, action: str, payload: dict[str, Any] | None = None) -> None:
        with self.db.session() as session:
            session.add(SessionCommand(key=key, action=action, payload=payload))

    def poll(self, key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        now = utcnow_naive()
        out: list[dict[str, Any]] = []
        with self.db.session() as session:
            commands = list(
                session.scalars(
                    select(SessionCommand)
                    .where(SessionCommand.key == key, SessionCommand.consumed_at.is_(None))
                    .order_by(SessionCommand.id)
                    .limit(limit)
                )
            )
            for command in commands:
                command.consumed_at = now
                out.append({"action": command.action, "payload": command.payload or {}})
        return out


class SessionCoordinator:
    """本实例的会话协调器：心跳续租、轮询命令、汇总远程会话。"""

    def __init__(
        self,
        *,
        db: Database,
        paper: Any,
        live: Any,
        instance_id: str | None = None,
        interval: float = 1.0,
        ttl: float = 8.0,
        on_event: Callable[[str, dict[str, Any], int | None], None] | None = None,
    ) -> None:
        self.instance_id = instance_id or uuid.uuid4().hex
        self.leases = LeaseStore(db, self.instance_id)
        self.paper = paper
        self.live = live
        self.interval = max(0.25, float(interval))
        self.ttl = max(self.interval * 2, float(ttl))
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="session-coordinator", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        self.leases.release_all()

    # ------------------------------------------------------------------ #
    def acquire(self, key: str, *, kind: str, user_id: int, run_id: str) -> bool:
        return self.leases.acquire(
            key,
            kind=kind,
            user_id=user_id,
            run_id=run_id,
            state={"run_id": run_id, "status": "preparing"},
            ttl=self.ttl,
        )

    def release(self, key: str) -> None:
        self.leases.release(key)

    def request(self, key: str, action: str, payload: dict[str, Any] | None = None) -> None:
        self.leases.enqueue(key, action, payload)

    def active(self, key: str) -> dict[str, Any] | None:
        lease = self.leases.get(key)
        if lease is None:
            return None
        try:
            expires = datetime.fromisoformat(lease["expires_at"])
        except (TypeError, ValueError):
            return None
        return lease if expires > utcnow_naive() else None

    def remote_sessions(self, user_id: int, kind: str) -> list[dict[str, Any]]:
        out = []
        for lease in self.leases.active_for_user(user_id):
            if lease["kind"] != kind or lease["owner"] == self.instance_id:
                continue
            state = lease.get("state") or {}
            if state:
                out.append(state)
        return out

    def remote_state(self, user_id: int, *, run_id: str | None = None, kind: str) -> dict[str, Any] | None:
        for lease in self.leases.active_for_user(user_id):
            if lease["kind"] != kind:
                continue
            if run_id is not None and lease["run_id"] != run_id:
                continue
            state = lease.get("state") or {}
            if state:
                return state
        return None

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - 协调器不能拖垮服务
                log.exception("会话协调器心跳失败")

    def _tick(self) -> None:
        for user_id, run_id, runner in self.paper.all_sessions():
            key = f"paper:{run_id}"
            if not runner.running:
                self.leases.release(key)
                continue
            state = summarize(runner.state())
            status = str(state.get("status") or "running")
            if not self.leases.renew(key, state=state, ttl=self.ttl, status=status):
                if not self.acquire(key, kind="paper", user_id=user_id, run_id=run_id):
                    # 租约被其他实例接管：本实例必须停掉引擎，避免重复交易
                    log.warning("纸盘会话 %s 的租约已被其他实例持有，停止本地引擎", run_id)
                    self.paper.stop(user_id, run_id, timeout=5)
                    continue
                self.leases.renew(key, state=state, ttl=self.ttl, status=status)
            for command in self.leases.poll(key):
                self._apply_paper(user_id, run_id, command)

        for user_id, credential_id, run_id, runner in self.live.all_sessions():
            key = f"live:{user_id}:{credential_id}"
            if not runner.running:
                self.leases.release(key)
                continue
            state = summarize(runner.state())
            status = str(state.get("status") or "running")
            if not self.leases.renew(key, state=state, ttl=self.ttl, status=status):
                if not self.acquire(key, kind="live", user_id=user_id, run_id=run_id):
                    log.warning("实盘会话 %s 的租约已被其他实例持有，停止本地引擎", run_id)
                    self.live.stop(user_id, credential_id, timeout=10)
                    continue
                self.leases.renew(key, state=state, ttl=self.ttl, status=status)
            for command in self.leases.poll(key):
                self._apply_live(user_id, credential_id, run_id, command)

    def _apply_paper(self, user_id: int, run_id: str, command: dict[str, Any]) -> None:
        action = command.get("action")
        payload = command.get("payload") or {}
        if action == "stop":
            self.paper.stop(user_id, run_id)
            self.leases.release(f"paper:{run_id}")
        elif action == "pause":
            engine = self.paper.engine(user_id, run_id)
            if engine:
                engine.pause()
        elif action == "resume":
            engine = self.paper.engine(user_id, run_id)
            if engine:
                engine.resume()
        elif action == "switch":
            engine = self.paper.engine(user_id, run_id)
            if engine:
                try:
                    engine.switch_strategy(
                        str(payload.get("strategy_id") or ""),
                        dict(payload.get("params") or {}),
                        cancel_orders=bool(payload.get("cancel_orders", True)),
                    )
                except Exception:  # noqa: BLE001
                    log.exception("跨实例切换纸盘策略失败 run_id=%s", run_id)

    def _apply_live(self, user_id: int, credential_id: int, run_id: str, command: dict[str, Any]) -> None:
        action = command.get("action")
        payload = command.get("payload") or {}
        if action == "stop":
            self.live.stop(user_id, credential_id)
            self.leases.release(f"live:{user_id}:{credential_id}")
        elif action == "pause":
            engine = self.live.engine(user_id, credential_id)
            if engine:
                engine.pause()
        elif action == "resume":
            engine = self.live.engine(user_id, credential_id)
            if engine:
                engine.resume()
        elif action == "switch":
            engine = self.live.engine(user_id, credential_id)
            if engine:
                try:
                    engine.switch_strategy(
                        str(payload.get("strategy_id") or ""),
                        dict(payload.get("params") or {}),
                        cancel_orders=bool(payload.get("cancel_orders", True)),
                    )
                except Exception:  # noqa: BLE001
                    log.exception("跨实例切换实盘策略失败 run_id=%s", run_id)


__all__ = ["LeaseStore", "SessionCoordinator", "summarize"]
