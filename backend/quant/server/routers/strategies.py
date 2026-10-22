"""策略路由：注册表暴露给前端 + 用户自建（条件拼装）策略 CRUD。

新增策略只要写一个类并加 ``@register_strategy``，这里自动出现，无需改代码。
拼装策略的 spec 在创建/更新/提交运行三条路径上都会过白名单校验。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...services import strategy_share_service
from ...storage.models import User, UserCustomStrategy
from ...storage.repositories import UserStrategyRepository
from ...strategy.custom import validate_spec
from ...strategy.registry import get_strategy, list_strategies
from ..deps import audit, get_ctx, get_current_user, get_db
from ..schemas import (
    CustomStrategyIn,
    CustomStrategyOut,
    CustomStrategyPatch,
    Message,
    SharedStrategyOut,
    StrategyOut,
    StrategyShareIn,
    StrategyShareOut,
)

router = APIRouter(prefix="/strategies", tags=["策略"])


def _out(record: UserCustomStrategy, *, update_available: bool = False) -> CustomStrategyOut:
    data = record.to_dict()
    data["update_available"] = update_available
    return CustomStrategyOut(**data)


def _validate(body: CustomStrategyIn | CustomStrategyPatch) -> dict | None:
    if body.spec is None:
        return None
    try:
        return validate_spec(body.spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"拼装策略 spec 无效：{exc}") from exc


@router.get("", response_model=list[StrategyOut], summary="策略列表（含参数 schema）")
def list_all(_: User = Depends(get_current_user)) -> list[StrategyOut]:
    return [StrategyOut(**schema) for schema in list_strategies()]


# -- 拼装策略 CRUD：必须声明在 /{strategy_id} 之前，否则会被通配路由捕获 ------- #


@router.get("/custom", response_model=list[CustomStrategyOut], summary="我的拼装策略列表")
def list_custom(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[CustomStrategyOut]:
    repo = UserStrategyRepository(session)
    return [
        _out(record, update_available=strategy_share_service.update_available(session, record, repo))
        for record in repo.list_for(user.id)
    ]


@router.post("/custom", response_model=CustomStrategyOut, status_code=201, summary="创建拼装策略")
def create_custom(
    body: CustomStrategyIn,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CustomStrategyOut:
    spec = _validate(body)
    assert spec is not None  # CustomStrategyIn.spec 必填
    repo = UserStrategyRepository(session)
    if any(record.name == body.name for record in repo.list_for(user.id)):
        raise HTTPException(status_code=409, detail=f"同名拼装策略已存在：{body.name}")
    category = str(spec.get("category", "trend"))
    description = str(spec.get("description", body.description))
    record = repo.create(
        user.id,
        name=body.name,
        spec=spec,
        description=body.description or description,
        category=category,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.custom.create",
        target=f"custom:{record.id}",
        detail={"name": record.name, "version": record.version},
    )
    return _out(
        record,
        update_available=strategy_share_service.update_available(session, record, repo),
    )


@router.patch("/custom/{strategy_pk}", response_model=CustomStrategyOut, summary="更新拼装策略")
def update_custom(
    strategy_pk: int,
    body: CustomStrategyPatch,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CustomStrategyOut:
    repo = UserStrategyRepository(session)
    record = repo.get(strategy_pk, owner_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"拼装策略 #{strategy_pk} 不存在")
    spec = _validate(body)
    if body.name is not None and body.name != record.name:
        if any(item.name == body.name for item in repo.list_for(user.id)):
            raise HTTPException(status_code=409, detail=f"同名拼装策略已存在：{body.name}")
    category = str(spec.get("category")) if spec and spec.get("category") else None
    record = repo.update(
        record,
        name=body.name,
        spec=spec,
        description=body.description,
        category=category,
        enabled=body.enabled,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.custom.update",
        target=f"custom:{record.id}",
        detail={"name": record.name, "version": record.version},
    )
    return _out(
        record,
        update_available=strategy_share_service.update_available(session, record, repo),
    )


@router.delete("/custom/{strategy_pk}", response_model=Message, summary="删除拼装策略")
def delete_custom(
    strategy_pk: int,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    repo = UserStrategyRepository(session)
    record = repo.get(strategy_pk, owner_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"拼装策略 #{strategy_pk} 不存在")
    name = record.name
    for fork in repo.forks_for_source(record.id):
        fork.source_strategy_id = None
        fork.source_version = None
    repo.delete(record)
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.custom.delete",
        target=f"custom:{strategy_pk}",
        detail={"name": name},
    )
    return Message(message=f"已删除拼装策略 {name}")


@router.get("/shared", response_model=list[SharedStrategyOut], summary="共享给我的策略")
def list_shared(
    user: User = Depends(get_current_user),
    ctx=Depends(get_ctx),
) -> list[SharedStrategyOut]:
    return [SharedStrategyOut(**item) for item in strategy_share_service.shared_for(ctx, user_id=user.id)]


@router.post(
    "/shared/{strategy_id}/fork",
    response_model=CustomStrategyOut,
    status_code=201,
    summary="加入共享策略到我的策略库",
)
def fork_shared(
    strategy_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    ctx=Depends(get_ctx),
):
    data = strategy_share_service.fork(ctx, user_id=user.id, strategy_id=strategy_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.share.fork",
        target=f"custom:{data['id']}",
        detail={"source": strategy_id, "version": data.get("source_version")},
    )
    return CustomStrategyOut(**data)


@router.post("/custom/{strategy_pk}/sync", response_model=CustomStrategyOut, summary="同步共享策略到最新版本")
def sync_custom(
    strategy_pk: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    ctx=Depends(get_ctx),
):
    data = strategy_share_service.sync(ctx, user_id=user.id, fork_id=strategy_pk)
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.share.sync",
        target=f"custom:{strategy_pk}",
        detail={"version": data["version"], "source_version": data.get("source_version")},
    )
    return CustomStrategyOut(**data)


@router.get("/custom/{strategy_pk}/shares", response_model=list[StrategyShareOut], summary="查看策略共享记录")
def list_shares(
    strategy_pk: int,
    user: User = Depends(get_current_user),
    ctx=Depends(get_ctx),
):
    return [
        StrategyShareOut(**item)
        for item in strategy_share_service.shares_for(ctx, user_id=user.id, strategy_id=strategy_pk)
    ]


@router.post(
    "/custom/{strategy_pk}/shares",
    response_model=StrategyShareOut,
    status_code=201,
    summary="共享策略（完全/点对点）",
)
def share_custom(
    strategy_pk: int,
    body: StrategyShareIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    ctx=Depends(get_ctx),
):
    data = strategy_share_service.share(
        ctx,
        user_id=user.id,
        strategy_id=strategy_pk,
        scope=body.scope,
        target_username=body.target_username,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.share.create",
        target=f"custom:{strategy_pk}",
        detail={"scope": data["scope"], "target": data.get("target_username")},
    )
    return StrategyShareOut(**data)


@router.delete("/custom/{strategy_pk}/shares/{share_id}", response_model=Message, summary="撤销共享")
def revoke_share(
    strategy_pk: int,
    share_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    ctx=Depends(get_ctx),
):
    strategy_share_service.revoke(ctx, user_id=user.id, strategy_id=strategy_pk, share_id=share_id)
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.share.revoke",
        target=f"custom:{strategy_pk}",
        detail={"share_id": share_id},
    )
    return Message(message="已撤销共享")


@router.get("/{strategy_id}", response_model=StrategyOut, summary="单个策略详情")
def get_one(strategy_id: str, _: User = Depends(get_current_user)) -> StrategyOut:
    try:
        return StrategyOut(**get_strategy(strategy_id).schema())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
