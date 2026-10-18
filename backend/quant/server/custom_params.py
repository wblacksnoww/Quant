"""回测/纸盘提交时的拼装策略参数处理。

spec 有两条进入引擎的路径：

1. ``params["custom_strategy_id"]`` —— 前端选择用户已保存的拼装策略，
   服务端按归属取 spec 注入（拼装策略仅本人可见，不做共享）；
2. ``params["spec"]`` —— 直接内联 spec（CLI/脚本场景），同样强制过白名单校验。

两条路径最终都会调 :func:`quant.strategy.custom.parse_spec` 校验，
保证引擎拿到的 spec 一定是安全的结构化数据。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..storage.models import User
from ..storage.repositories import UserStrategyRepository
from ..strategy.custom import validate_spec


def inject_custom_spec(params: dict[str, Any], *, user: User, session: Session) -> dict[str, Any]:
    """返回一个注入并校验过 spec 的 params 副本（不修改入参）。"""
    custom_id = params.get("custom_strategy_id")
    if custom_id is None and params.get("spec") is None:
        return params

    resolved = dict(params)
    if custom_id is not None:
        try:
            strategy_pk = int(custom_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="custom_strategy_id 必须是整数") from exc
        record = UserStrategyRepository(session).get(strategy_pk, owner_id=user.id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"拼装策略 #{strategy_pk} 不存在")
        if not record.enabled:
            raise HTTPException(status_code=422, detail=f"拼装策略 #{strategy_pk} 已停用")
        resolved["spec"] = record.spec

    try:
        resolved["spec"] = validate_spec(resolved.get("spec"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"拼装策略 spec 无效：{exc}") from exc
    return resolved


__all__ = ["inject_custom_spec"]
