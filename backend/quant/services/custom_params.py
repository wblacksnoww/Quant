"""拼装策略参数注入（框架无关版本）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..core.errors import InvalidRequest, NotFoundError
from ..storage.repositories import UserStrategyRepository
from ..strategy.custom import validate_spec


def inject_custom_spec(params: dict[str, Any], *, user_id: int, session: Session) -> dict[str, Any]:
    """返回一个注入并校验过 spec 的 params 副本（不修改入参）。"""
    custom_id = params.get("custom_strategy_id")
    if custom_id is None and params.get("spec") is None:
        return params

    resolved = dict(params)
    if custom_id is not None:
        try:
            strategy_pk = int(custom_id)
        except (TypeError, ValueError) as exc:
            raise InvalidRequest("custom_strategy_id 必须是整数") from exc
        record = UserStrategyRepository(session).get(strategy_pk, owner_id=user_id)
        if record is None:
            raise NotFoundError(f"拼装策略 #{strategy_pk} 不存在")
        if not record.enabled:
            raise InvalidRequest(f"拼装策略 #{strategy_pk} 已停用")
        resolved["spec"] = record.spec

    try:
        resolved["spec"] = validate_spec(resolved.get("spec"))
    except ValueError as exc:
        raise InvalidRequest(f"拼装策略 spec 无效：{exc}") from exc
    return resolved


__all__ = ["inject_custom_spec"]
