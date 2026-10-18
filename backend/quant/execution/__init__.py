"""执行层：成本模型、A 股撮合规则、Broker。"""

from .broker import Broker, SimBroker
from .cost import CostModel
from .matching import BlockReason, MatchingEngine, MatchOutcome
from .rules import (
    BOARDS,
    BoardRules,
    board_of,
    clamp_to_limits,
    is_valid_lot,
    normalize_symbol,
    price_limits,
    round_down_lot,
    rules_for,
)

__all__ = [
    "BOARDS",
    "BlockReason",
    "BoardRules",
    "Broker",
    "CostModel",
    "MatchOutcome",
    "MatchingEngine",
    "SimBroker",
    "board_of",
    "clamp_to_limits",
    "is_valid_lot",
    "normalize_symbol",
    "price_limits",
    "round_down_lot",
    "rules_for",
]
