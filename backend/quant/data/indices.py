"""指数行情：内置指数清单 + 腾讯批量快照（A股 / 港股 / 美股）。

指数**不可交易**，因此不进入 `MARKET:CODE` 体系（避免与股票代码冲突，例如
上证指数 000001 与平安银行 000001）。这里用腾讯的指数代码（``sh000001`` /
``hkHSI`` / ``usDJI``）作为稳定 id，供行情页的指数卡片使用。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import requests

from ..core.errors import DataProviderUnavailable

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_QUOTE_URL = "https://qt.gtimg.cn/q="


@dataclass(frozen=True, slots=True)
class IndexDef:
    id: str  # 腾讯指数代码
    name: str
    market: str  # CN / HK / US
    currency: str


INDICES: tuple[IndexDef, ...] = (
    IndexDef("sh000001", "上证指数", "CN", "CNY"),
    IndexDef("sz399001", "深证成指", "CN", "CNY"),
    IndexDef("sz399006", "创业板指", "CN", "CNY"),
    IndexDef("sh000300", "沪深300", "CN", "CNY"),
    IndexDef("sh000905", "中证500", "CN", "CNY"),
    IndexDef("sh000688", "科创50", "CN", "CNY"),
    IndexDef("hkHSI", "恒生指数", "HK", "HKD"),
    IndexDef("hkHSCEI", "国企指数", "HK", "HKD"),
    IndexDef("hkHSTECH", "恒生科技", "HK", "HKD"),
    IndexDef("usDJI", "道琼斯", "US", "USD"),
    IndexDef("usIXIC", "纳斯达克", "US", "USD"),
    IndexDef("usINX", "标普500", "US", "USD"),
)
_BY_ID = {item.id: item for item in INDICES}


@dataclass(slots=True)
class IndexQuote:
    id: str
    name: str
    market: str
    currency: str
    price: float
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "market": self.market,
            "currency": self.currency,
            "price": round(self.price, 4),
            "prev_close": round(self.prev_close, 4) if self.prev_close is not None else None,
            "change": round(self.change, 4) if self.change is not None else None,
            "change_pct": round(self.change_pct, 4) if self.change_pct is not None else None,
        }


def _num(value: object) -> float | None:
    try:
        text = str(value).strip()
        if text in ("", "-", "--"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def fetch_indices(ids: Iterable[str] | None = None) -> list[IndexQuote]:
    """批量取指数快照（一次 HTTP 请求，覆盖三个市场）。"""
    wanted = [item for item in (ids or [d.id for d in INDICES]) if item in _BY_ID]
    if not wanted:
        return []
    try:
        resp = requests.get(
            _QUOTE_URL + ",".join(wanted),
            headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=8,
        )
        resp.encoding = "gbk"
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise DataProviderUnavailable(f"指数行情获取失败: {exc}") from exc

    out: list[IndexQuote] = []
    for line in resp.text.strip().splitlines():
        head, _, body = line.partition("=")
        code = head.replace("v_", "").strip()
        definition = _BY_ID.get(code)
        if definition is None:
            continue
        fields = body.strip().rstrip(";").strip('"').split("~")
        if len(fields) < 33:
            continue
        price = _num(fields[3])
        prev = _num(fields[4])
        if price is None or price <= 0:
            continue
        out.append(
            IndexQuote(
                id=code,
                name=fields[1] or definition.name,
                market=definition.market,
                currency=definition.currency,
                price=price,
                prev_close=prev,
                change=_num(fields[31]),
                change_pct=_num(fields[32]),
            )
        )
    return out


__all__ = ["INDICES", "IndexDef", "IndexQuote", "fetch_indices"]
