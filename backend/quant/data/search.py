"""标的检索：腾讯 smartbox 统一入口（A 股 / 港股 / 美股）。

smartbox 是腾讯行情页的联想接口，无密钥、直连可用，覆盖三个市场::

    https://smartbox.gtimg.cn/s3/?v=2&q=关键词&t=all
    v_hint="sz~159915~创业板ETF易方达~cybetfyfd~ETF^sh~600519~贵州茅台~gzmt~GP-A"

约定：

* 条目按 ``~`` 分字段、按 ``^`` 分多条：``market~code~name~pinyin~type``；
* ``market``：``sh/sz/bj`` → A 股，``hk`` → 港股，``us`` → 美股；
  其它前缀（``jj`` 场外基金等）不在系统范围内，直接过滤；
* ``type``：``GP*`` → 股票，``ETF`` → 场内基金；``ZS``（指数）、``KJ``（开放式
  基金）等一律过滤 —— 指数代码与股票代码有重叠（如 ``sh000001`` 上证指数 vs
  ``sz000001`` 平安银行），本系统的规范 id 不含交易所，不做区分；
* 美股代码带交易所后缀（``aapl.oq`` / ``brk.b.n`` / ``tcehy.ps``），统一剥掉；
* A 股补足 6 位、港股补足 5 位，输出规范 id（``CN:600519`` / ``US:AAPL`` / ``HK:00700``）。
"""

from __future__ import annotations

import logging

import requests

from ..core.markets import strip_us_suffix
from .base import SymbolInfo

log = logging.getLogger(__name__)

_SMARTBOX_URL = "https://smartbox.gtimg.cn/s3/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_TIMEOUT = 5.0

_MARKETS = {"sh": "CN", "sz": "CN", "bj": "CN", "hk": "HK", "us": "US"}


def _strip_us_suffix(code: str) -> str:
    """``AAPL.OQ`` → ``AAPL``；``BRK.B.N`` → ``BRK.B``（统一实现见 core.markets）。"""
    return strip_us_suffix(code)


def _kind_of(raw: str) -> str | None:
    text = str(raw or "").upper()
    if text.startswith("GP"):
        return "stock"
    if text.startswith("ETF"):
        return "etf"
    return None


def parse_smartbox(text: str) -> list[SymbolInfo]:
    """解析 smartbox 响应，输出规范化的 :class:`SymbolInfo` 列表。"""
    from ..core.markets import Instrument, Market
    from ..execution.rules import board_of

    out: list[SymbolInfo] = []
    seen: set[str] = set()
    for line in text.splitlines():
        _, _, body = line.partition('v_hint="')
        if not body:
            continue
        for entry in body.rstrip('";').split("^"):
            parts = entry.split("~")
            if len(parts) < 5:
                continue
            market = _MARKETS.get(parts[0].strip().lower())
            kind = _kind_of(parts[4])
            if market is None or kind is None:
                continue
            raw_code = parts[1].strip().upper()
            if market == "US":
                code = _strip_us_suffix(raw_code)
                if not code:
                    continue
            else:
                digits = "".join(ch for ch in raw_code if ch.isdigit())
                if not digits:
                    continue
                code = digits.zfill(5 if market == "HK" else 6)
            inst = Instrument(Market(market), code)
            if inst.id in seen:
                continue
            seen.add(inst.id)
            name = parts[2].strip()
            out.append(
                SymbolInfo(
                    symbol=inst.id,
                    name=name,
                    board=board_of(inst.id).name if market == "CN" else inst.market.label,
                    is_st="ST" in name.upper(),
                    market=market,
                    kind=kind,
                )
            )
    return out


def search_symbols(
    keyword: str, *, limit: int = 20, market: str | None = None, timeout: float = _TIMEOUT
) -> list[SymbolInfo]:
    """统一检索；``market`` 可限定 ``CN``/``US``/``HK``（``None`` 为全部）。"""
    keyword = str(keyword).strip()
    if not keyword:
        return []
    response = requests.get(
        _SMARTBOX_URL,
        params={"v": "2", "q": keyword, "t": "all"},
        headers={"User-Agent": _UA},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    items = parse_smartbox(response.text)
    wanted = str(market).strip().upper() if market else None
    if wanted and wanted not in ("ALL", "全部"):
        items = [item for item in items if item.market == wanted]
    return items[: max(1, int(limit))]


__all__ = ["parse_smartbox", "search_symbols"]
