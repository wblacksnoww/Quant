"""策略注册表。

新增策略**不需要改路由、不需要改配置**——只要类被导入且带 ``@register_strategy``，
API ``/api/strategies`` 与前端下拉框就会自动出现它。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Iterable

from .base import Strategy

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Strategy]] = {}
_DISCOVERED = False


def register_strategy(cls: type[Strategy]) -> type[Strategy]:
    """类装饰器：注册策略。"""

    sid = getattr(cls, "strategy_id", "") or cls.__name__.lower()
    if not sid:
        raise ValueError(f"{cls.__name__} 缺少 strategy_id")
    if sid in _REGISTRY and _REGISTRY[sid] is not cls:
        raise ValueError(f"策略 id 重复：{sid}")
    cls.strategy_id = sid
    cls.display_name = getattr(cls, "display_name", "") or cls.__name__
    _REGISTRY[sid] = cls
    return cls


def discover(package: str = "quant.strategy.builtin") -> None:
    """导入内置策略包，触发所有 ``@register_strategy``。幂等。"""
    global _DISCOVERED
    if _DISCOVERED:
        return
    try:
        module = importlib.import_module(package)
    except ImportError:
        log.warning("内置策略包 %s 不存在", package)
        return
    for info in pkgutil.iter_modules(module.__path__):
        importlib.import_module(f"{package}.{info.name}")
    _DISCOVERED = True


def get_strategy(strategy_id: str) -> type[Strategy]:
    discover()
    if strategy_id not in _REGISTRY:
        raise KeyError(f"未知策略 {strategy_id!r}，可用：{sorted(_REGISTRY)}")
    return _REGISTRY[strategy_id]


def list_strategies() -> list[dict]:
    discover()
    return [cls.schema() for cls in sorted(_REGISTRY.values(), key=lambda c: c.strategy_id)]


def strategy_ids() -> list[str]:
    discover()
    return sorted(_REGISTRY)


def all_strategies() -> Iterable[type[Strategy]]:
    discover()
    return _REGISTRY.values()


__all__ = [
    "all_strategies",
    "discover",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "strategy_ids",
]
