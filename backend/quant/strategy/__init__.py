"""策略层：基类、上下文、注册表、指标库、条件拼装器。"""

from .base import ParamSpec, Strategy, StrategyContext
from .custom import CustomRuleStrategy
from .registry import (
    all_strategies,
    discover,
    get_strategy,
    list_strategies,
    register_strategy,
    strategy_ids,
)

discover()

__all__ = [
    "CustomRuleStrategy",
    "ParamSpec",
    "Strategy",
    "StrategyContext",
    "all_strategies",
    "discover",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "strategy_ids",
]
