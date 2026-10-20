"""实时快照：腾讯 → 东财 → 新浪 多源聚合（A 股 / 美股 / 港股）。

A 股实时行情没有"官方免费 API"，可用的公开接口各有各的限流/封禁节奏：
腾讯 ``qt.gtimg.cn``、东财 ``push2.eastmoney.com``、新浪 ``hq.sinajs.cn``。
单靠一家，遇到限流（表现为 TLS 正常但请求被重置）整个实时行情就空了；
这里按顺序尝试、缺的标的自动从下一家补齐，全部失败才抛
:class:`DataProviderUnavailable`，由上层回退"最后已知价"。

市场覆盖：

* 腾讯：三市场全支持（``sh600519`` / ``hk00700`` / ``usAAPL``），字段布局基本一致；
* 新浪：三市场全支持（``sh600519`` / ``rt_hk00700`` / ``gb_aapl``），字段布局各不同；
* 东财：仅 A 股（``1.600519`` / ``0.000001``），美股交易所代码（105/106/107）无法可靠推断。

统一口径（与 ``quant.core.markets`` 一致）：

* 键 / ``RealtimeQuote.symbol`` 一律是规范 id（``CN:600519`` / ``US:AAPL`` / ``HK:00700``）；
* ``volume`` 单位统一为**股**（腾讯 A 股口径是手，这里已换算）；
* ``amount`` 单位是本币元（腾讯 A 股口径是万元，这里已换算）；
* ``currency`` 按市场给出，供前端加货币符号。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import requests

from ..core.errors import DataProviderUnavailable
from ..core.markets import Instrument, Market, parse_instrument, strip_us_suffix

log = logging.getLogger(__name__)

_FETCH_TIMEOUT = 4.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# 美股在腾讯行情里的交易所后缀缓存（AAPL → AAPL.OQ），K 线接口需要完整代码
_US_SUFFIX_CACHE: dict[str, str] = {}


@dataclass(slots=True)
class RealtimeQuote:
    """统一结构的实时快照。``price`` 为 0 表示数据源未给出有效价。"""

    symbol: str
    name: str = ""
    price: float = 0.0
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None  # 股
    amount: float | None = None  # 本币元
    change: float | None = None
    change_pct: float | None = None
    ts: str = ""  # 数据源时间（留空表示未提供）
    source: str = ""
    stale: bool = False
    market: str = "CN"
    currency: str = "CNY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "prev_close": self.prev_close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "amount": self.amount,
            "change": self.change,
            "change_pct": self.change_pct,
            "ts": self.ts,
            "source": self.source,
            "stale": self.stale,
            "market": self.market,
            "currency": self.currency,
        }


# --------------------------------------------------------------------------- #
# 代码换算
# --------------------------------------------------------------------------- #


def exchange_prefix(symbol: str) -> str:
    """规范标的 → 交易所前缀（``sh``/``sz``/``bj``/``hk``/``us``）。"""
    inst = parse_instrument(symbol)
    if inst.market is Market.HK:
        return "hk"
    if inst.market is Market.US:
        return "us"
    code = inst.code
    if code.startswith(("43", "83", "87", "88", "92")):
        return "bj"
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def tencent_code(inst: Instrument) -> str:
    """腾讯行情代码：``sh600519`` / ``hk00700`` / ``usAAPL``。"""
    return f"{exchange_prefix(inst.id)}{inst.code}"


def sina_code(inst: Instrument) -> str:
    """新浪行情代码：``sh600519`` / ``rt_hk00700`` / ``gb_aapl``。"""
    if inst.market is Market.US:
        return f"gb_{inst.code.lower()}"
    if inst.market is Market.HK:
        return f"rt_hk{inst.code}"
    return f"{exchange_prefix(inst.id)}{inst.code}"


def _em_secid(inst: Instrument) -> str | None:
    """东财 secid（仅 A 股）：``1.600519`` / ``0.000001``。"""
    if inst.market is not Market.CN:
        return None
    return f"{'1' if exchange_prefix(inst.id) == 'sh' else '0'}.{inst.code}"


def _num(value: Any) -> float | None:
    """宽容转 float：''、'-'、None 等占位符 → None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # NaN → None


def _fmt_ts(raw: str) -> str:
    """行情时间统一成 ``YYYY-MM-DD HH:MM:SS``；无法识别时原样返回。"""
    text = str(raw or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    return text.replace("/", "-")


def _strip_us_suffix(code: str) -> str:
    """``AAPL.OQ`` → ``AAPL``；``BRK.B.N`` → ``BRK.B``（统一实现见 core.markets）。"""
    return strip_us_suffix(code)


# --------------------------------------------------------------------------- #
# 解析（独立函数便于单测）
# --------------------------------------------------------------------------- #


def parse_tencent(text: str) -> dict[str, RealtimeQuote]:
    """腾讯 ``qt.gtimg.cn``：``v_sh600519="1~贵州茅台~600519~现价~昨收~今开~..."``。"""
    out: dict[str, RealtimeQuote] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        head, _, body = line.partition('="')
        prefix = head.removeprefix("v_")[:2].lower()
        if prefix not in ("sh", "sz", "bj", "hk", "us"):
            continue
        fields = body.rstrip('";').split("~")
        if len(fields) < 39 or not fields[2]:
            continue
        market = {"hk": Market.HK, "us": Market.US}.get(prefix, Market.CN)
        if market is Market.US:
            raw_code = fields[2].upper()
            code = _strip_us_suffix(raw_code)
            if raw_code != code:
                _US_SUFFIX_CACHE[code] = raw_code
        else:
            digits = "".join(ch for ch in fields[2] if ch.isdigit())
            code = digits.zfill(5 if market is Market.HK else 6)
        if market is Market.CN:
            volume = _num(fields[36])  # 手
            volume = volume * 100 if volume is not None else None
            amount = _num(fields[37])  # 万元
            amount = amount * 10000 if amount is not None else None
        else:
            volume = _num(fields[36])  # 股
            amount = _num(fields[37])  # 本币元
        inst = Instrument(market, code)
        out[inst.id] = RealtimeQuote(
            symbol=inst.id,
            name=fields[1] or (fields[46] if len(fields) > 46 else ""),
            price=_num(fields[3]) or 0.0,
            prev_close=_num(fields[4]),
            open=_num(fields[5]),
            high=_num(fields[33]),
            low=_num(fields[34]),
            volume=volume,
            amount=amount,
            change=_num(fields[31]),
            change_pct=_num(fields[32]),
            ts=_fmt_ts(fields[30]),
            source="tencent",
            market=market.value,
            currency=market.currency,
        )
    return out


def parse_eastmoney(payload: dict[str, Any]) -> dict[str, RealtimeQuote]:
    """东财 ``push2.ulist.np/get``：``{"data": {"diff": [{"f12": "600519", ...}]}}``（仅 A 股）。"""
    diff = ((payload.get("data") or {}).get("diff")) or []
    if isinstance(diff, dict):  # 某些端点在只有一条时返回 dict
        diff = list(diff.values())
    out: dict[str, RealtimeQuote] = {}
    for row in diff:
        if not isinstance(row, dict) or not row.get("f12"):
            continue
        raw = str(row["f12"])
        if "." in raw:  # 形如 1.600519 的 secid 防御处理
            raw = raw.split(".")[-1]
        code = "".join(ch for ch in raw if ch.isdigit()).zfill(6)
        inst = Instrument(Market.CN, code)
        out[inst.id] = RealtimeQuote(
            symbol=inst.id,
            name=str(row.get("f14") or ""),
            price=_num(row.get("f2")) or 0.0,
            prev_close=_num(row.get("f18")),
            open=_num(row.get("f17")),
            high=_num(row.get("f15")),
            low=_num(row.get("f16")),
            volume=_num(row.get("f5")),  # 手 → 下面统一换算
            amount=_num(row.get("f6")),
            change=_num(row.get("f4")),
            change_pct=_num(row.get("f3")),
            ts="",
            source="eastmoney",
            market="CN",
            currency="CNY",
        )
        if out[inst.id].volume is not None:
            out[inst.id].volume = out[inst.id].volume * 100  # 手 → 股
    return out


def parse_sina(text: str) -> dict[str, RealtimeQuote]:
    """新浪 ``hq.sinajs.cn``，三种布局：

    * A 股：``var hq_str_sh600519="名称,今开,昨收,现价,最高,最低,...,股数,金额,...,日期,时间";``
    * 美股：``var hq_str_gb_aapl="名称,现价,涨跌%,时间,涨跌额,今开,最高,最低,...,股数,...,昨收";``
    * 港股：``var hq_str_rt_hk00700="英文名,中文名,今开,昨收,最高,最低,现价,涨跌额,涨跌幅,...,金额,股数,...,日期,时间";``
    """
    out: dict[str, RealtimeQuote] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        head, _, body = line.partition('="')
        key = head.removeprefix("var hq_str_").strip()
        fields = body.rstrip('";').split(",")
        if key.startswith("gb_"):  # 美股
            if len(fields) < 31 or not fields[0]:
                continue
            inst = Instrument(Market.US, key[3:].upper())
            out[inst.id] = RealtimeQuote(
                symbol=inst.id,
                name=fields[0],
                price=_num(fields[1]) or 0.0,
                prev_close=_num(fields[26]),
                open=_num(fields[5]),
                high=_num(fields[6]),
                low=_num(fields[7]),
                volume=_num(fields[10]),  # 股
                amount=_num(fields[30]),
                change=_num(fields[4]),
                change_pct=_num(fields[2]),
                ts=_fmt_ts(fields[3]),
                source="sina",
                market="US",
                currency="USD",
            )
        elif key.startswith("rt_hk"):  # 港股
            if len(fields) < 19 or not fields[1]:
                continue
            inst = Instrument(Market.HK, "".join(ch for ch in key[5:] if ch.isdigit()))
            out[inst.id] = RealtimeQuote(
                symbol=inst.id,
                name=fields[1],
                price=_num(fields[6]) or 0.0,
                prev_close=_num(fields[3]),
                open=_num(fields[2]),
                high=_num(fields[4]),
                low=_num(fields[5]),
                volume=_num(fields[12]),  # 股
                amount=_num(fields[11]),
                change=_num(fields[7]),
                change_pct=_num(fields[8]),
                ts=_fmt_ts(f"{fields[17]} {fields[18]}"),
                source="sina",
                market="HK",
                currency="HKD",
            )
        elif key[:2] in ("sh", "sz", "bj"):  # A 股
            if len(fields) < 32 or not fields[0]:
                continue
            code = "".join(ch for ch in key[2:] if ch.isdigit()).zfill(6)
            inst = Instrument(Market.CN, code)
            quote = RealtimeQuote(
                symbol=inst.id,
                name=fields[0],
                price=_num(fields[3]) or 0.0,
                prev_close=_num(fields[2]),
                open=_num(fields[1]),
                high=_num(fields[4]),
                low=_num(fields[5]),
                volume=_num(fields[8]),  # 股
                amount=_num(fields[9]),
                change=None,
                change_pct=None,
                ts=_fmt_ts(f"{fields[30].replace('/', '-')} {fields[31]}"),
                source="sina",
                market="CN",
                currency="CNY",
            )
            if quote.prev_close and quote.price:
                quote.change = round(quote.price - quote.prev_close, 3)
                quote.change_pct = round((quote.price / quote.prev_close - 1) * 100, 3)
            out[inst.id] = quote
    return out


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #


def _fetch_tencent(instruments: list[Instrument], timeout: float) -> dict[str, RealtimeQuote]:
    joined = ",".join(tencent_code(inst) for inst in instruments)
    response = requests.get(
        "https://qt.gtimg.cn/q=" + joined,
        headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    return parse_tencent(response.text)


def _fetch_eastmoney(instruments: list[Instrument], timeout: float) -> dict[str, RealtimeQuote]:
    secids = [secid for inst in instruments if (secid := _em_secid(inst)) is not None]
    if not secids:
        return {}
    response = requests.get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "fltt": "2",
            "invt": "2",
            "fields": "f2,f3,f4,f5,f6,f8,f12,f14,f15,f16,f17,f18",
            "secids": ",".join(secids),
        },
        headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_eastmoney(response.json())


def _fetch_sina(instruments: list[Instrument], timeout: float) -> dict[str, RealtimeQuote]:
    joined = ",".join(sina_code(inst) for inst in instruments)
    response = requests.get(
        "https://hq.sinajs.cn/list=" + joined,
        headers={"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    return parse_sina(response.text)


_SOURCES: tuple[tuple[str, Callable[[list[Instrument], float], dict[str, RealtimeQuote]]], ...] = (
    ("腾讯", _fetch_tencent),
    ("东财", _fetch_eastmoney),
    ("新浪", _fetch_sina),
)


def fetch_quotes(symbols: Iterable[str], *, timeout: float = _FETCH_TIMEOUT) -> dict[str, RealtimeQuote]:
    """多源批量快照。返回已取到的标的（键为规范 id）；全部失败抛 ``DataProviderUnavailable``。"""
    instruments: list[Instrument] = []
    seen: set[str] = set()
    for raw in symbols:
        text = str(raw).strip()
        if not text:
            continue
        try:
            inst = parse_instrument(text)
        except ValueError:
            log.info("忽略无法解析的标的: %r", raw)
            continue
        if inst.id not in seen:
            seen.add(inst.id)
            instruments.append(inst)
    if not instruments:
        return {}

    merged: dict[str, RealtimeQuote] = {}
    errors: list[str] = []
    for name, fetch in _SOURCES:
        missing = [inst for inst in instruments if inst.id not in merged]
        if not missing:
            break
        try:
            got = fetch(missing, timeout)
        except Exception as exc:  # noqa: BLE001 —— 单源失败继续下一家
            message = f"{name}: {type(exc).__name__} {str(exc)[:80]}"
            errors.append(message)
            log.info("实时源不可用 %s", message)
            continue
        for inst in missing:
            if got.get(inst.id) is not None:
                merged[inst.id] = got[inst.id]

    if not merged:
        raise DataProviderUnavailable("实时行情数据源均不可用（" + "；".join(errors) + "）")
    return merged


def us_kline_code(code: str) -> str:
    """美股在腾讯 K 线接口里的代码（含交易所后缀，如 ``AAPL.OQ``）。

    后缀从实时快照顺路缓存；未命中时联网解析一次，失败则退回裸代码。
    """
    code = str(code).upper()
    if code in _US_SUFFIX_CACHE:
        return _US_SUFFIX_CACHE[code]
    try:
        fetch_quotes([f"US:{code}"])
    except Exception:  # noqa: BLE001
        pass
    return _US_SUFFIX_CACHE.get(code, code)


__all__ = [
    "RealtimeQuote",
    "exchange_prefix",
    "fetch_quotes",
    "parse_eastmoney",
    "parse_sina",
    "parse_tencent",
    "sina_code",
    "tencent_code",
    "us_kline_code",
]
