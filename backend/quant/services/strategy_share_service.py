"""拼装策略共享、fork 与版本同步。"""

from __future__ import annotations

from typing import Any

from ..core.errors import ConflictError, InvalidRequest, NotFoundError, PermissionDenied
from ..storage.repositories import (
    StrategyShareRepository,
    UserRepository,
    UserStrategyRepository,
)


def _with_update(record: Any, source: Any | None) -> dict[str, Any]:
    data = record.to_dict()
    data["update_available"] = bool(
        source is not None
        and record.source_strategy_id is not None
        and source.version > (record.source_version or 0)
    )
    return data


def update_available(session: Any, record: Any, repo: UserStrategyRepository) -> bool:
    if not record.source_strategy_id:
        return False
    source = repo.get(record.source_strategy_id)
    return bool(source and source.version > (record.source_version or 0))


def shared_for(ctx: Any, *, user_id: int) -> list[dict[str, Any]]:
    with ctx.db.session() as session:
        share_repo = StrategyShareRepository(session)
        strategy_repo = UserStrategyRepository(session)
        users = UserRepository(session)
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for share, strategy in share_repo.visible_for(user_id):
            if strategy.id in seen or not strategy.enabled:
                continue
            seen.add(strategy.id)
            owner = users.get(strategy.user_id)
            fork = strategy_repo.find_fork(user_id, strategy.id)
            out.append(
                {
                    "share_id": share.id,
                    "share_type": "public" if share.target_user_id is None else "user",
                    "strategy_id": strategy.id,
                    "name": strategy.name,
                    "description": strategy.description,
                    "category": strategy.category,
                    "spec": strategy.spec,
                    "version": strategy.version,
                    "owner_username": owner.username if owner else "",
                    "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
                    "fork_id": fork.id if fork else None,
                    "fork_version": fork.version if fork else None,
                    "update_available": bool(fork and strategy.version > (fork.source_version or 0)),
                }
            )
        return out


def fork(ctx: Any, *, user_id: int, strategy_id: int) -> dict[str, Any]:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        share_repo = StrategyShareRepository(session)
        source = strategy_repo.get(strategy_id)
        if source is None:
            raise NotFoundError("策略不存在")
        if source.user_id == user_id:
            raise ConflictError("不能加入自己的策略")
        if not source.enabled:
            raise ConflictError("该策略已停用，无法加入")
        if not share_repo.exists_for(strategy_id, user_id):
            raise PermissionDenied("该策略没有共享给你")
        if strategy_repo.find_fork(user_id, strategy_id) is not None:
            raise ConflictError("该策略已加入你的策略库")
        base = source.name
        existing_names = {item.name for item in strategy_repo.list_for(user_id)}
        name = base
        index = 2
        while name in existing_names:
            name = f"{base} ({index})"
            index += 1
        record = strategy_repo.create(
            user_id,
            name=name,
            spec=dict(source.spec or {}),
            description=source.description,
            category=source.category,
            source_strategy_id=source.id,
            source_version=source.version,
        )
        return _with_update(record, source)


def sync(ctx: Any, *, user_id: int, fork_id: int) -> dict[str, Any]:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        share_repo = StrategyShareRepository(session)
        fork_record = strategy_repo.get(fork_id, owner_id=user_id)
        if fork_record is None:
            raise NotFoundError("策略不存在")
        if not fork_record.source_strategy_id:
            raise InvalidRequest("该策略不是共享副本，无法同步")
        source = strategy_repo.get(fork_record.source_strategy_id)
        if source is None:
            raise ConflictError("源策略已删除")
        if source.user_id != user_id and not share_repo.exists_for(source.id, user_id):
            raise PermissionDenied("源策略已取消共享")
        if source.version <= (fork_record.source_version or 0):
            raise ConflictError("已经是最新版本")
        record = strategy_repo.update(
            fork_record,
            spec=dict(source.spec or {}),
            description=source.description,
            category=source.category,
            source_version=source.version,
        )
        return _with_update(record, source)


def share(
    ctx: Any, *, user_id: int, strategy_id: int, scope: str, target_username: str | None
) -> dict[str, Any]:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        share_repo = StrategyShareRepository(session)
        users = UserRepository(session)
        strategy = strategy_repo.get(strategy_id, owner_id=user_id)
        if strategy is None:
            raise NotFoundError("策略不存在")
        target_id: int | None = None
        if scope == "user":
            username = (target_username or "").strip()
            if not username:
                raise InvalidRequest("点对点共享需要填写目标用户名")
            target = users.get_by_username(username)
            if target is None:
                raise NotFoundError("目标用户不存在")
            if target.id == user_id:
                raise InvalidRequest("不能共享给自己")
            target_id = target.id
        elif scope != "public":
            raise InvalidRequest("scope 只支持 public / user")
        existing = share_repo.find(strategy.id, target_id)
        if existing is not None:
            record = existing
        else:
            record = share_repo.create(strategy_id=strategy.id, owner_id=user_id, target_user_id=target_id)
        target = users.get(record.target_user_id) if record.target_user_id else None
        return {
            "id": record.id,
            "scope": "public" if record.target_user_id is None else "user",
            "target_username": target.username if target else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }


def shares_for(ctx: Any, *, user_id: int, strategy_id: int) -> list[dict[str, Any]]:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        strategy = strategy_repo.get(strategy_id, owner_id=user_id)
        if strategy is None:
            raise NotFoundError("策略不存在")
        users = UserRepository(session)
        out = []
        for item in StrategyShareRepository(session).list_for_strategy(strategy_id):
            target = users.get(item.target_user_id) if item.target_user_id else None
            out.append(
                {
                    "id": item.id,
                    "scope": "public" if item.target_user_id is None else "user",
                    "target_username": target.username if target else None,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
            )
        return out


def revoke(ctx: Any, *, user_id: int, strategy_id: int, share_id: int) -> None:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        if strategy_repo.get(strategy_id, owner_id=user_id) is None:
            raise NotFoundError("策略不存在")
        share_repo = StrategyShareRepository(session)
        record = share_repo.get(share_id)
        if record is None or record.strategy_id != strategy_id:
            raise NotFoundError("共享记录不存在")
        share_repo.delete(record)


def admin_list(ctx: Any) -> list[dict[str, Any]]:
    with ctx.db.session() as session:
        strategy_repo = UserStrategyRepository(session)
        share_repo = StrategyShareRepository(session)
        users = UserRepository(session)
        out = []
        for record in strategy_repo.list_all():
            owner = users.get(record.user_id)
            out.append(
                {
                    **record.to_dict(),
                    "owner_username": owner.username if owner else "",
                    "share_count": share_repo.count_for_strategy(record.id),
                    "fork_count": len(strategy_repo.forks_for_source(record.id)),
                }
            )
        return out


def admin_update(ctx: Any, *, strategy_id: int, enabled: bool) -> dict[str, Any]:
    with ctx.db.session() as session:
        repo = UserStrategyRepository(session)
        record = repo.get(strategy_id)
        if record is None:
            raise NotFoundError("策略不存在")
        return repo.update(record, enabled=enabled).to_dict()


def admin_delete(ctx: Any, *, strategy_id: int) -> str:
    with ctx.db.session() as session:
        repo = UserStrategyRepository(session)
        record = repo.get(strategy_id)
        if record is None:
            raise NotFoundError("策略不存在")
        name = record.name
        for fork in repo.forks_for_source(record.id):
            fork.source_strategy_id = None
            fork.source_version = None
        repo.delete(record)
        return name


__all__ = [
    "admin_delete",
    "admin_list",
    "admin_update",
    "fork",
    "share",
    "shared_for",
    "shares_for",
    "sync",
    "update_available",
]
