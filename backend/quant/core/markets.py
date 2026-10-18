"""市场与标的标识：全系统统一的符号体系。

规范形式 ``MARKET:CODE``：

* ``CN:600519`` — A 股（6 位数字代码）
* ``US:AAPL``   — 美股（字母代码，可含 ``.``/``-``，如 ``BRK.B``）
* ``HK:00700``  — 港股（5 位数字代码）

兼容输入（用户输入 / 旧数据 / 外部接口）：

* ``600519``、``sh600519``、``SH.600519``、``600519.SH`` → ``CN:600519``
* ``aapl``、``AAPL.US`` → ``US:AAPL``
* ``700``、``hk700``、``0700.HK`` → ``HK:00700``

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

    @property
    def label(self) -> str:
        return {"CN": "A股", "US": "美股", "HK": "港股"}[self.value]

    @property
    def currency(self) -> str:
        return {"CN": "CNY", "US": "USD", "HK": "HKD"}[self.value]


_CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$", "HKD": "HK$"}

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
}

# 美股代码：字母开头，允许数字/点/连字符（BRK.B、RDS-A 等）
_US_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
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
