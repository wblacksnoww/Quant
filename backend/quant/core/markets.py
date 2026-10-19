"""市场与标的标识：全系统统一的符号体系。

规范形式 ``MARKET:CODE``：

* ``CN:600519`` — A 股（6 位数字代码）
* ``US:AAPL``   — 美股（字母代码，可含 ``.``/``-``，如 ``BRK.B``）
* ``HK:00700``  — 港股（5 位数字代码）
* ``CRYPTO:BTC/USDT`` — 虚拟货币（BASE/QUOTE 交易对，**必须显式前缀**）
* ``FUND:007005`` — 场外基金（6 位数字代码，**必须显式前缀**，裸 6 位仍归 A 股）

兼容输入（用户输入 / 旧数据 / 外部接口）：

* ``600519``、``sh600519``、``SH.600519``、``600519.SH`` → ``CN:600519``
* ``aapl``、``AAPL.US`` → ``US:AAPL``
* ``700``、``hk700``、``0700.HK`` → ``HK:00700``
* ``cc:eth/usdt`` → ``CRYPTO:ETH/USDT``

约定：**解析只发生在边界**（API、引擎配置、数据源入口），
内部一律使用规范 id，缓存 / 事件 / 订单 / 规则全部以它为准。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Market(str, Enum):
    """支持的证券市场。"""

    CN = "CN"
    US = "US"
    HK = "HK"
    CRYPTO = "CRYPTO"
    FUND = "FUND"

    @property
    def label(self) -> str:
        return {
            "CN": "A股",
            "US": "美股",
            "HK": "港股",
            "CRYPTO": "虚拟货币",
            "FUND": "基金",
        }[self.value]

    @property
    def currency(self) -> str:
        return {
            "CN": "CNY",
            "US": "USD",
            "HK": "HKD",
            "CRYPTO": "USDT",
            "FUND": "CNY",
        }[self.value]


_CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$", "HKD": "HK$", "USDT": "USDT"}

_MARKET_ALIASES: dict[str, Market] = {
    "CN": Market.CN,
    "SH": Market.CN,
    "SZ": Market.CN,
    "BJ": Market.CN,
    "SS": Market.CN,
    "US": Market.US,
    "NASDAQ": Market.US,
    "NYSE": Market.US,
    "HK": Market.HK,
    "HKG": Market.HK,
    "CRYPTO": Market.CRYPTO,
    "CC": Market.CRYPTO,
    "FUND": Market.FUND,
    "OF": Market.FUND,
}

# 美股代码：字母开头，允许数字/点/连字符（BRK.B、RDS-A 等）
_US_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
# 虚拟货币交易对：BASE/QUOTE（USDT 计价为主，也允许 BTC/ETH 等计价）
_CRYPTO_RE = re.compile(r"^[A-Z0-9]{1,20}/[A-Z0-9]{1,20}$")
_PREFIX_RE = re.compile(r"^(SH|SZ|BJ|HK|US)(\w.*)$")

# 腾讯行情里的美股交易所后缀（AAPL.OQ / TSLA.N / TCEHY.PS ...），解析时剥掉
US_EXCHANGE_SUFFIXES = {"OQ", "N", "AM", "PS", "NY", "OB", "PK", "OP"}


def strip_us_suffix(code: str) -> str:
    """``AAPL.OQ`` → ``AAPL``；``BRK.B.N`` → ``BRK.B``（只剥已知交易所后缀）。"""
    parts = str(code).upper().split(".")
    while len(parts) > 1 and parts[-1] in US_EXCHANGE_SUFFIXES:
        parts.pop()
    return ".".join(parts)


def currency_symbol(currency: str) -> str:
    """货币代码 → 展示符号（未知货币原样返回）。"""
    return _CURRENCY_SYMBOLS.get(str(currency).upper(), str(currency))


def _validate_code(market: Market, raw: str) -> str:
    """按市场校验并规范化代码，不合法时抛 ``ValueError``。"""
    text = str(raw).strip().upper()
    if market is Market.CN:
        if text.isdigit() and 1 <= len(text) <= 6:
            return text.zfill(6)
        raise ValueError(f"不是合法的 A 股代码: {raw}")
    if market is Market.HK:
        if text.isdigit() and 1 <= len(text) <= 5:
            return text.zfill(5)
        raise ValueError(f"不是合法的港股代码: {raw}")
    if market is Market.CRYPTO:
        if _CRYPTO_RE.fullmatch(text):
            return text
        raise ValueError(f"不是合法的虚拟货币交易对: {raw}")
    if market is Market.FUND:
        if text.isdigit() and 1 <= len(text) <= 6:
            return text.zfill(6)
        raise ValueError(f"不是合法的基金代码: {raw}")
    stripped = strip_us_suffix(text)
    if _US_RE.fullmatch(stripped):
        return stripped
    raise ValueError(f"不是合法的美股代码: {raw}")


@dataclass(frozen=True, slots=True)
class Instrument:
    """规范标的：市场 + 代码。"""

    market: Market
    code: str

    @property
    def id(self) -> str:
        """规范 id（``MARKET:CODE``），全系统对外统一口径。"""
        return f"{self.market.value}:{self.code}"

    @property
    def currency(self) -> str:
        return self.market.currency

    @property
    def label(self) -> str:
        return f"{self.market.label} {self.code}"

    def __str__(self) -> str:
        return self.id


def parse_instrument(text: str) -> Instrument:
    """把任意常见写法解析成 :class:`Instrument`，无法识别时抛 ``ValueError``。"""
    raw = str(text).strip().upper()
    if not raw:
        raise ValueError("标的不能为空")
    if raw in _MARKET_ALIASES:
        raise ValueError(f"缺少代码: {text}")

    # 1) 显式前缀：MARKET:CODE / MARKET.CODE
    for alias, market in _MARKET_ALIASES.items():
        for sep in (":", "."):
            prefix = alias + sep
            if raw.startswith(prefix) and len(raw) > len(prefix):
                try:
                    return Instrument(market, _validate_code(market, raw[len(prefix) :]))
                except ValueError:
                    continue

    # 2) 无分隔符前缀：SH600519 / hk00700 / usAAPL
    match = _PREFIX_RE.match(raw)
    if match and len(match.group(2)) >= 2:
        market = _MARKET_ALIASES[match.group(1)]
        try:
            return Instrument(market, _validate_code(market, match.group(2)))
        except ValueError:
            pass

    # 3) 后缀形式：600519.SH / AAPL.US / 0700.HK
    if "." in raw:
        head, _, tail = raw.rpartition(".")
        if tail in _MARKET_ALIASES and head:
            try:
                return Instrument(_MARKET_ALIASES[tail], _validate_code(_MARKET_ALIASES[tail], head))
            except ValueError:
                pass

    # 4) 启发式：6 位数字 → A 股；1~5 位数字 → 港股；字母代码 → 美股
    if raw.isdigit():
        if len(raw) == 6:
            return Instrument(Market.CN, raw)
        if len(raw) <= 5:
            return Instrument(Market.HK, raw.zfill(5))
        raise ValueError(f"无法识别的数字代码: {text}")
    if _US_RE.fullmatch(raw):
        return Instrument(Market.US, strip_us_suffix(raw))
    raise ValueError(f"无法识别的标的代码: {text}")


def try_parse(text: str) -> Instrument | None:
    """宽容版解析：失败返回 ``None``。"""
    try:
        return parse_instrument(text)
    except ValueError:
        return None


def normalize_id(text: str) -> str:
    """任意写法 → 规范 id。"""
    return parse_instrument(text).id


def single_market(symbols: Iterable[str]) -> Market:
    """校验一批标的属于同一市场（不同货币无法统一记账）。"""
    markets = {parse_instrument(s).market for s in symbols}
    if len(markets) > 1:
        names = "、".join(sorted(m.label for m in markets))
        raise ValueError(f"同一运行只能包含单一市场的标的（当前混用了 {names}）")
    return markets.pop() if markets else Market.CN


__all__ = [
    "Instrument",
    "Market",
    "US_EXCHANGE_SUFFIXES",
    "currency_symbol",
    "normalize_id",
    "parse_instrument",
    "single_market",
    "strip_us_suffix",
    "try_parse",
]


# --------------------------------------------------------------------------- #
# 市场微观规则：板块 / 涨跌停（原 execution.rules 上移；data/risk 共用的本源）
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

# 虚拟货币：7×24 无涨跌停、T+0、任意数量（小数）、8 位报价精度
_CRYPTO = BoardRules("crypto", "虚拟货币", 0.0, 1, t_plus_1=False, price_digits=8)
# 场外基金：净值制申购/赎回（不走引擎撮合），份额小数、4 位净值精度
_FUND_OTC = BoardRules("fund_otc", "场外基金", 0.0, 1, t_plus_1=False, price_digits=4)

BOARDS: dict[str, BoardRules] = {
    b.code: b
    for b in (_MAIN, _GEM, _STAR, _BSE, _FUND, _INDEX, _US, _HK, _CRYPTO, _FUND_OTC)
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
        if inst.market is Market.CRYPTO:
            return _CRYPTO
        if inst.market is Market.FUND:
            return _FUND_OTC
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
    # 指数代码（000300 等）与股票代码共用同一数字空间，默认按主板处理
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
