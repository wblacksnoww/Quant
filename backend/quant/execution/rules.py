"""市场微观规则：板块识别、涨跌停价、最小交易单位、T+1/交收制度。

把"这只票属于哪个市场、哪个板块、涨跌停几个点、一手多少股"集中在这里，
撮合引擎与风控都从这里取数，避免规则散落各处。

市场差异（近似，够用即可，别假装精确到每家券商）：

* **A 股**：T+1、涨跌停按板块、整手 100/200 股。
* **美股**：T+0、无涨跌停、1 股起买；常规时段外不做处理。
* **港股**：T+0、无涨跌停；每手股数因票而异，统一按 100 股近似。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.markets import Market, parse_instrument

# --------------------------------------------------------------------------- #
# 板块
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BoardRules:
    """单个板块的交易规则。"""

    code: str
    name: str
    limit_pct: float  # 涨跌停幅度
    lot_size: int  # 买入最小单位（股）
    t_plus_1: bool = True
    price_digits: int = 2

    @property
    def limit_label(self) -> str:
        return f"±{self.limit_pct * 100:.0f}%"


_MAIN = BoardRules("main", "沪深主板", 0.10, 100)
_GEM = BoardRules("gem", "创业板", 0.20, 100)
_STAR = BoardRules("star", "科创板", 0.20, 200)
_BSE = BoardRules("bse", "北交所", 0.30, 100)
_FUND = BoardRules("fund", "场内基金/ETF", 0.10, 100)
_INDEX = BoardRules("index", "指数", 0.0, 1, t_plus_1=False, price_digits=4)

# 境外市场：无涨跌停、T+0；港股每手股数因票而异，这里按 100 股近似
_US = BoardRules("us", "美股", 0.0, 1, t_plus_1=False)
_HK = BoardRules("hk", "港股", 0.0, 100, t_plus_1=False, price_digits=3)

BOARDS: dict[str, BoardRules] = {
    b.code: b for b in (_MAIN, _GEM, _STAR, _BSE, _FUND, _INDEX, _US, _HK)
}

_ST = BoardRules("main_st", "主板ST", 0.05, 100)


def normalize_symbol(symbol: str) -> str:
    """统一成规范代码：``sh600519`` → ``600519``；``AAPL``/``700`` 原样规范化。"""
    try:
        return parse_instrument(symbol).code
    except ValueError:
        text = str(symbol).strip().upper()
        for prefix in ("SH.", "SZ.", "BJ.", "SH:", "SZ:", "BJ:", "SH", "SZ", "BJ"):
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :]
                break
        text = text.lstrip(".")
        return text.zfill(6) if text.isdigit() else text


def board_of(symbol: str) -> BoardRules:
    """按市场与代码前缀判断板块。"""
    try:
        inst = parse_instrument(symbol)
    except ValueError:
        inst = None
    if inst is not None:
        if inst.market is Market.US:
            return _US
        if inst.market is Market.HK:
            return _HK
        code = inst.code
    else:
        code = normalize_symbol(symbol)
    if code.startswith(("688", "689")):
        return _STAR
    if code.startswith(("300", "301")):
        return _GEM
    if code.startswith(("8", "43", "92")):
        return _BSE
    if code.startswith(("51", "56", "58", "15", "16", "588", "159")):
        return _FUND
    if code.startswith(("000300", "000001", "399001", "399006")) and len(code) == 6:
        # 指数代码与股票代码有重叠，仅在显式声明时区分，默认仍按主板处理
        return _MAIN
    return _MAIN


def rules_for(symbol: str, *, is_st: bool = False) -> BoardRules:
    """取板块规则；主板 ST 股票涨跌停收窄到 ±5%。"""
    board = board_of(symbol)
    if is_st and board.code == _MAIN.code:
        return _ST
    return board


# --------------------------------------------------------------------------- #
# 涨跌停价
# --------------------------------------------------------------------------- #


def _round_price(value: float, digits: int = 2) -> float:
    """四舍五入到分（A 股报价精度）。"""
    factor = 10**digits
    # 用 Decimal 语义避免浮点 0.005 边界问题
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def price_limits(
    prev_close: float,
    symbol: str,
    *,
    is_st: bool = False,
    board: BoardRules | None = None,
) -> tuple[float | None, float | None]:
    """返回 ``(涨停价, 跌停价)``。``prev_close`` 缺失或为 0 时返回 ``(None, None)``。"""
    if not prev_close or prev_close <= 0:
        return None, None
    rules = board or rules_for(symbol, is_st=is_st)
    if rules.limit_pct <= 0:
        return None, None
    up = _round_price(prev_close * (1 + rules.limit_pct), rules.price_digits)
    down = _round_price(prev_close * (1 - rules.limit_pct), rules.price_digits)
    return up, down


def clamp_to_limits(
    price: float, prev_close: float, symbol: str, *, is_st: bool = False
) -> float:
    """把价格夹到涨跌停区间内（防止策略报出越界限价）。"""
    up, down = price_limits(prev_close, symbol, is_st=is_st)
    if up is None or down is None:
        return price
    return min(max(price, down), up)


# --------------------------------------------------------------------------- #
# 数量校验
# --------------------------------------------------------------------------- #


def is_valid_lot(quantity: int, symbol: str, *, is_full_exit: bool = False) -> bool:
    """买入必须整手；卖出允许零股，但只在"清仓"时允许不足一手的尾巴。"""
    if quantity <= 0:
        return False
    lot = rules_for(symbol).lot_size
    if lot <= 1:
        return True
    if quantity % lot == 0:
        return True
    return is_full_exit


def round_down_lot(quantity: float, symbol: str) -> int:
    """向下取整到整手。"""
    lot = rules_for(symbol).lot_size
    if lot <= 1:
        return int(quantity)
    return int(quantity // lot) * lot


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
