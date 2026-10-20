"""策略路由：注册表暴露给前端 + 用户自建（条件拼装）策略 CRUD。

新增策略只要写一个类并加 ``@register_strategy``，这里自动出现，无需改代码。
拼装策略的 spec 在创建/更新/提交运行三条路径上都会过白名单校验。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...storage.models import User, UserCustomStrategy
from ...storage.repositories import UserStrategyRepository
from ...strategy.custom import validate_spec
from ...strategy.registry import get_strategy, list_strategies
from ..deps import audit, get_current_user, get_db
from ..schemas import CustomStrategyIn, CustomStrategyOut, CustomStrategyPatch, Message, StrategyOut

router = APIRouter(prefix="/strategies", tags=["策略"])


def _out(record: UserCustomStrategy) -> CustomStrategyOut:
    return CustomStrategyOut(**record.to_dict())


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
    return [_out(record) for record in UserStrategyRepository(session).list_for(user.id)]


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
        detail={"name": record.name},
    )
    return _out(record)


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
    record = repo.update(
        record,
        name=body.name,
        spec=spec,
        description=body.description,
        enabled=body.enabled,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="strategy.custom.update",
        target=f"custom:{record.id}",
        detail={"name": record.name},
    )
    return _out(record)


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


@router.get("/{strategy_id}", response_model=StrategyOut, summary="单个策略详情")
def get_one(strategy_id: str, _: User = Depends(get_current_user)) -> StrategyOut:
    try:
        return StrategyOut(**get_strategy(strategy_id).schema())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
