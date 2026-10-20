"""市场微观规则：板块识别、涨跌停价、最小交易单位、T+1/交收制度。

把"这只票属于哪个市场、哪个板块、涨跌停几个点、一手多少股"集中在这里，
撮合引擎与风控都从这里取数，避免规则散落各处。

市场差异（近似，够用即可，别假装精确到每家券商）：

* **A 股**：T+1、涨跌停按板块、整手 100/200 股。
* **美股**：T+0、无涨跌停、1 股起买；常规时段外不做处理。
* **港股**：T+0、无涨跌停；每手股数因票而异，统一按 100 股近似。
* **虚拟货币**：T+0、无涨跌停、任意数量（含小数）。
* **场外基金**：不走撮合，数量规则仅作兜底（任意数量）。
"""

from __future__ import annotations

import math

from ..core.markets import (  # noqa: F401 —— 市场微观规则本体在 core，这里只保留再导出
    BOARDS,
    BoardRules,
    Market,
    board_of,
    clamp_to_limits,
    normalize_symbol,
    parse_instrument,
    price_limits,
    rules_for,
)

# --------------------------------------------------------------------------- #
# 数量校验
# --------------------------------------------------------------------------- #

_LOT_TOL = 1e-9


def is_valid_lot(quantity: float, symbol: str, *, is_full_exit: bool = False) -> bool:
    """买入必须整手；卖出允许零股，但只在"清仓"时允许不足一手的尾巴。

    整手市场（lot_size > 1）要求数量是整手的整数倍（带浮点容差）；
    任意数量市场（lot_size <= 1，如美/虚拟货币）任何正数都合法。
    """
    if quantity <= 0:
        return False
    lot = rules_for(symbol).lot_size
    if lot <= 1:
        return True
    rem = quantity % lot
    if rem <= _LOT_TOL or lot - rem <= _LOT_TOL:
        return True
    return is_full_exit


def round_down_lot(quantity: float, symbol: str) -> float:
    """向下取整到整手。任意数量市场原样返回（不截断小数）。"""
    lot = rules_for(symbol).lot_size
    if lot <= 1:
        return float(quantity)
    return math.floor(quantity / lot) * lot


__all__ = [
    "BOARDS",
    "BoardRules",
    "board_of",
    "clamp_to_limits",
    "is_valid_lot",
    "normalize_symbol",
    "price_limits",
    "round_down_lot",
    "rules_for",
]
