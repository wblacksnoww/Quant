"""akshare 数据源（A 股 / 美股 / 港股日线、实时快照、标的检索）。

akshare 是惰性导入的：没有网络或没装 akshare 时，其它数据源（CSV / synthetic）
仍可正常使用，服务不会因为一个可选依赖起不来。

日线主源与回退：

* A 股：``ak.stock_zh_a_hist``（东财）→ 腾讯 K 线 ``fqkline/get``；
* 美股：``ak.stock_us_daily``（新浪）→ 腾讯 K 线 ``usfqkline/get``；
* 港股：``ak.stock_hk_daily``（新浪）→ 腾讯 K 线 ``hkfqkline/get``。

成交量单位随市场原生口径（A 股=手，美股/港股=股），实时快照统一为股，
两者差异见 :mod:`quant.data.realtime` 的模块注释。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd
import requests

from ...core.errors import DataNotFound, DataProviderUnavailable
from ...core.markets import Market, parse_instrument, try_parse
from ..base import DataProvider, SymbolInfo, normalize_frame, register_provider
from ..realtime import RealtimeQuote, fetch_quotes, us_kline_code
from ..search import search_symbols

log = logging.getLogger(__name__)

_TENCENT_KLINE = {
    "CN": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "HK": "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get",
    "US": "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get",
}
_TENCENT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# 排行榜数据源：新浪行情中心（东财对 Python 客户端的 TLS 指纹会直接断开连接）
_SINA_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_SINA_HK_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHKStockData"
)
_SINA_RANK_NODE = {"CN": "hs_a", "HK": "qbgg_hk"}

# 美股没有稳定的免费全市场排行接口：用高关注度标的池 + 多源实时快照排序
_US_RANK_POOL = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK.B", "JPM",
    "LLY", "V", "XOM", "UNH", "MA", "COST", "PG", "JNJ", "HD", "MRK",
    "ABBV", "CVX", "PEP", "KO", "AMD", "NFLX", "CRM", "ADBE", "INTC", "BABA",
)


def _retry(fn, *, attempts: int = 3, backoff: float = 0.8, what: str = "akshare"):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise DataProviderUnavailable(f"{what} 请求失败（重试 {attempts} 次）: {last}") from last


@register_provider("akshare")
class AkShareProvider(DataProvider):
    """免 token 的多市场数据源。

    * 日线：A 股 / 美股 / 港股分别走 akshare 对应接口，失败自动回退腾讯 K 线；
    * 实时：腾讯 → 东财 → 新浪 多源聚合（见 ``quant.data.realtime``），三市场全覆盖；
    * 检索：腾讯 smartbox 统一入口，失败时回退 akshare A 股代码表。
    * 覆盖 ``CN``/``US``/``HK``；虚拟货币与场外基金由专门的 Provider 负责。
    """

    markets = ("CN", "US", "HK")
    supports_realtime = True
    supports_search = True

    def __init__(self, retries: int = 3, timeout: float | None = None) -> None:
        self.retries = retries
        self.timeout = timeout
        self._name_cache: dict[str, str] | None = None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _ak():
        try:
            import akshare as ak  # noqa: PLC0415 - 惰性导入
        except ImportError as exc:  # pragma: no cover
            raise DataProviderUnavailable("未安装 akshare，请 pip install akshare") from exc
        return ak

    # ------------------------------------------------------------------ #
    # 日线
    # ------------------------------------------------------------------ #
    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        inst = parse_instrument(symbol)
        if inst.market is Market.CN:
            return self._daily_cn(inst, start, end, adjust=adjust)
        return self._daily_overseas(inst, start, end, adjust=adjust)

    def _daily_cn(self, inst, start: date, end: date, *, adjust: str) -> pd.DataFrame:
        try:
            ak = self._ak()
            raw = _retry(
                lambda: ak.stock_zh_a_hist(
                    symbol=inst.code,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust=adjust if adjust in ("qfq", "hfq", "") else "qfq",
                ),
                attempts=self.retries,
                what=f"akshare {inst.code}",
            )
            if raw is None or len(raw) == 0:
                raise DataNotFound(f"akshare 没有 {inst.id} 在 {start}~{end} 的数据")
            frame = normalize_frame(raw, symbol=inst.id)
        except (DataProviderUnavailable, DataNotFound) as primary:
            log.warning("akshare 日线不可用（%s），尝试腾讯回退", primary)
            try:
                frame = tencent_daily(inst.id, start, end, adjust=adjust)
            except Exception as backup:  # noqa: BLE001
                # 容器/机房 IP 上腾讯 fqkline 可能被风控（501），再用新浪兜底
                try:
                    frame = sina_daily_cn(inst.id, start, end)
                except Exception as fallback:  # noqa: BLE001
                    raise DataProviderUnavailable(
                        f"{inst.id} 日线获取失败：akshare({primary})；腾讯({backup})；新浪({fallback})"
                    ) from fallback

        # A 股涨跌停用前一交易日收盘推算，数据源不直接提供
        from ...execution.rules import price_limits

        ups, downs = [], []
        for prev_close in frame["prev_close"].tolist():
            up, down = price_limits(prev_close or 0.0, inst.id)
            ups.append(up)
            downs.append(down)
        frame["limit_up"] = ups
        frame["limit_down"] = downs
        return frame

    def _daily_overseas(self, inst, start: date, end: date, *, adjust: str) -> pd.DataFrame:
        fn_name = "stock_us_daily" if inst.market is Market.US else "stock_hk_daily"
        kicker = "美股" if inst.market is Market.US else "港股"
        adjust = adjust if adjust in ("qfq", "hfq", "") else "qfq"
        try:
            ak = self._ak()
            raw = _retry(
                lambda: getattr(ak, fn_name)(symbol=inst.code, adjust=adjust),
                attempts=self.retries,
                what=f"akshare {kicker} {inst.code}",
            )
            if raw is None or len(raw) == 0:
                raise DataNotFound(f"akshare 没有 {inst.id} 的{kicker}日线")
            frame = normalize_frame(raw, symbol=inst.id)
            mask = (frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))
            frame = frame.loc[mask].reset_index(drop=True)
            if frame.empty:
                raise DataNotFound(f"akshare 没有 {inst.id} 在 {start}~{end} 的数据")
        except (DataProviderUnavailable, DataNotFound) as primary:
            log.warning("akshare %s 日线不可用（%s），尝试腾讯回退", kicker, primary)
            try:
                frame = tencent_daily(inst.id, start, end, adjust=adjust)
            except Exception as backup:  # noqa: BLE001
                raise DataProviderUnavailable(
                    f"{inst.id} 日线获取失败：akshare({primary})；腾讯({backup})"
                ) from backup
        # 境外市场无涨跌停，limit_up/limit_down 保持 None
        return frame

    # ------------------------------------------------------------------ #
    # 实时
    # ------------------------------------------------------------------ #
    def snapshot(self, symbols: Iterable[str]) -> dict[str, RealtimeQuote]:
        """多源聚合实时快照（腾讯 → 东财 → 新浪），三市场全覆盖。"""
        return fetch_quotes(symbols)

    def realtime(self, symbols: Iterable[str]) -> dict[str, float]:
        return {
            code: quote.price for code, quote in self.snapshot(symbols).items() if quote.price > 0
        }

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(
        self, keyword: str = "", limit: int = 50, *, market: str | None = None
    ) -> list[SymbolInfo]:
        keyword = str(keyword or "").strip()
        wanted = str(market).strip().upper() if market else None
        if wanted in ("ALL", "全部"):
            wanted = None
        try:
            items = search_symbols(keyword, limit=limit, market=wanted)
        except Exception as exc:  # noqa: BLE001 - 检索服务不可用时退代码表
            log.warning("smartbox 检索失败: %s", exc)
            items = []
        if items:
            return items[:limit]
        if wanted in (None, "CN"):
            return self._search_cn(keyword, limit)
        return []

    def _search_cn(self, keyword: str, limit: int) -> list[SymbolInfo]:
        """A 股代码表回退（smartbox 不可用 / 无结果时）。"""
        from ...execution.rules import board_of

        if self._name_cache is None:
            ak = self._ak()
            raw = _retry(lambda: ak.stock_info_a_code_name(), attempts=2, what="akshare 代码表")
            self._name_cache = {
                str(row["code"]).zfill(6): str(row["name"]) for _, row in raw.iterrows()
            }
        out: list[SymbolInfo] = []
        for code, name in self._name_cache.items():
            if keyword and keyword not in code and keyword not in name:
                continue
            out.append(
                SymbolInfo(
                    symbol=f"CN:{code}",
                    name=name,
                    board=board_of(code).name,
                    is_st="ST" in name.upper(),
                    market="CN",
                    kind="stock",
                )
            )
            if len(out) >= limit:
                break
        return out

    def info(self, symbol: str) -> SymbolInfo:
        from ...execution.rules import board_of

        inst = parse_instrument(symbol)
        for item in self.search(inst.code, limit=10, market=inst.market.value):
            if item.symbol == inst.id:
                return item
        return SymbolInfo(symbol=inst.id, board=board_of(inst.id).name, market=inst.market.value)

    # ------------------------------------------------------------------ #
    # 分钟 K 线（今日/本周视图，暂只支持 A 股）
    # ------------------------------------------------------------------ #
    def intraday(self, symbol: str, *, freq: str = "5m", limit: int = 240) -> pd.DataFrame:
        inst = parse_instrument(symbol)
        if inst.market is not Market.CN:
            raise NotImplementedError(f"{self.name} 暂不支持 {inst.market.label} 分钟线")
        return sina_intraday_cn(symbol, freq=freq, limit=limit)

    # ------------------------------------------------------------------ #
    # 排行榜（新浪行情中心；美股用热门池 + 多源快照）
    # ------------------------------------------------------------------ #
    def ranking(
        self,
        market: str,
        *,
        sort: str = "change_pct",
        order: str = "desc",
        limit: int = 20,
    ) -> list[RealtimeQuote]:
        key = str(market or "").strip().upper()
        if key == "US":
            return _rank_items(_us_ranking_quotes(), sort=sort, order=order, limit=limit)
        node = _SINA_RANK_NODE.get(key)
        if node is None:
            raise NotImplementedError(f"akshare 不支持 {market} 的排行榜")
        return _sina_ranking(key, node, sort=sort, order=order, limit=limit)


def _to_float(value: Any) -> float | None:
    """新浪返回的数值都是字符串，统一转换（容忍空值/千分位）。"""
    try:
        text = str(value).strip().replace(",", "")
        if text in ("", "-", "None", "null", "nan"):
            return None
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _rank_items(
    items: list[RealtimeQuote], *, sort: str, order: str, limit: int
) -> list[RealtimeQuote]:
    if sort == "amount":
        key_fn = lambda q: q.amount or 0.0  # noqa: E731
    elif sort == "change":
        key_fn = lambda q: q.change or 0.0  # noqa: E731
    else:
        key_fn = lambda q: q.change_pct or 0.0  # noqa: E731
    items.sort(key=key_fn, reverse=str(order).lower() != "asc")
    return items[:limit]


def _sina_ranking(
    market: str,
    node: str,
    *,
    sort: str,
    order: str,
    limit: int,
) -> list[RealtimeQuote]:
    """新浪行情中心排行榜（A 股支持全市场，港股为全部港股）。"""
    url = _SINA_RANK_URL if market == "CN" else _SINA_HK_RANK_URL
    params = {
        "page": 1,
        "num": max(limit, 50),
        "sort": "amount" if sort == "amount" else "changepercent",
        "asc": 1 if str(order).lower() == "asc" else 0,
        "node": node,
    }
    try:
        resp = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": _TENCENT_UA,
                "Referer": "https://vip.stock.finance.sina.com.cn/mkt/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:  # noqa: BLE001 - 网络/反爬统一转领域错误
        raise DataProviderUnavailable(f"新浪 {market} 排行榜请求失败: {exc}") from exc
    if not isinstance(rows, list):
        return []

    currency = "CNY" if market == "CN" else "HKD"
    out: list[RealtimeQuote] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if market == "CN":
            code = str(row.get("code") or "").strip().zfill(6)
            price = _to_float(row.get("trade"))
            prev = _to_float(row.get("settlement"))
        else:
            code = str(row.get("symbol") or "").strip().zfill(5)
            price = _to_float(row.get("lasttrade"))
            prev = _to_float(row.get("prevclose"))
        if not code or price is None or price <= 0:
            continue
        symbol = f"{market}:{code}"
        if try_parse(symbol) is None:
            continue
        out.append(
            RealtimeQuote(
                symbol=symbol,
                name=str(row.get("name") or "").strip(),
                price=price,
                prev_close=prev,
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                volume=_to_float(row.get("volume")),
                amount=_to_float(row.get("amount")),
                change=_to_float(row.get("pricechange")),
                change_pct=_to_float(row.get("changepercent")),
                source="sina:rank",
                market=market,
                currency=currency,
            )
        )
    return _rank_items(out, sort=sort, order=order, limit=limit)


def _us_ranking_quotes() -> list[RealtimeQuote]:
    """美股没有稳定的免费全市场排行：高关注度标的池 + 多源实时快照。"""
    try:
        quotes = fetch_quotes([f"US:{code}" for code in _US_RANK_POOL])
    except Exception as exc:  # noqa: BLE001
        raise DataProviderUnavailable(f"美股榜单快照失败: {exc}") from exc
    return list(quotes.values())


_SINA_KLINE_CN = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
)


_INTRADAY_SCALE = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


def _sina_kline_frame(symbol: str, scale: int, datalen: int) -> pd.DataFrame:
    """新浪 K 线公共实现（scale=5/15/30/60 分钟，240=日线）。"""
    import json

    inst = parse_instrument(symbol)
    if inst.market is not Market.CN:
        raise DataNotFound(f"新浪 K 线只实现了 A 股: {symbol}")
    prefix = "sh" if inst.code.startswith(("6", "9")) else (
        "bj" if inst.code.startswith(("4", "8")) else "sz"
    )
    try:
        resp = requests.get(
            _SINA_KLINE_CN,
            params={
                "symbol": f"{prefix}{inst.code}",
                "scale": scale,
                "ma": "no",
                "datalen": max(10, min(1023, datalen)),
            },
            headers={"User-Agent": _TENCENT_UA, "Referer": "https://finance.sina.com.cn/"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise DataProviderUnavailable(f"新浪 K 线 {inst.id} 请求失败: {exc}") from exc

    text = resp.text
    left, right = text.find("["), text.rfind("]")
    if left < 0 or right <= left:
        raise DataNotFound(f"新浪 K 线 {inst.id} 返回格式异常")
    try:
        rows = json.loads(text[left : right + 1])
    except ValueError as exc:
        raise DataNotFound(f"新浪 K 线 {inst.id} 解析失败") from exc

    frame = pd.DataFrame(
        [
            {
                "date": row.get("day"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
                "amount": row.get("amount") or 0.0,
            }
            for row in rows
            if isinstance(row, dict)
        ]
    )
    if len(frame) == 0:
        raise DataNotFound(f"新浪 K 线 {inst.id} 无数据")
    return normalize_frame(frame, symbol=inst.id)


def sina_intraday_cn(symbol: str, freq: str = "5m", limit: int = 240) -> pd.DataFrame:
    """新浪 A 股分钟 K 线（今日/本周视图）。"""
    scale = _INTRADAY_SCALE.get(freq)
    if scale is None:
        raise ValueError(f"不支持的分钟频率: {freq}")
    return _sina_kline_frame(symbol, scale, limit)


def sina_daily_cn(symbol: str, start: date, end: date) -> pd.DataFrame:
    """新浪 A 股日线（腾讯 fqkline 被风控时的兜底，如容器内 501）。"""
    datalen = max(30, min(1023, (end - start).days + 20))
    out = _sina_kline_frame(symbol, 240, datalen)
    out = out[(out["date"].dt.date >= start) & (out["date"].dt.date <= end)]
    if len(out) == 0:
        inst = parse_instrument(symbol)
        raise DataNotFound(f"新浪 K 线 {inst.id} 区间 {start}~{end} 无数据")
    return out.reset_index(drop=True)


def tencent_daily(symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
    """腾讯 K 线回退源：单次最多 640 根，按结束日期向前分页，三市场各用各的端点。

    返回与 akshare 路径一致的标准 schema（腾讯不提供成交额，补 0）；
    成交量单位保持市场原生口径：A 股=手，美股/港股=股。
    """
    from ..realtime import exchange_prefix

    inst = parse_instrument(symbol)
    kind = "hfq" if adjust == "hfq" else "qfq"
    if inst.market is Market.US:
        tcode = f"us{us_kline_code(inst.code)}"
    elif inst.market is Market.HK:
        tcode = f"hk{inst.code}"
    else:
        tcode = f"{exchange_prefix(inst.id)}{inst.code}"
    url = _TENCENT_KLINE[inst.market.value]

    page_end = end
    seen: set[str] = set()
    collected: list[list[str]] = []
    for _ in range(12):  # 最多 12 页 ≈ 30 年
        if page_end < start:
            break
        param = f"{tcode},day,{start.isoformat()},{page_end.isoformat()},640,{kind}"
        response = requests.get(
            url,
            params={"param": param},
            headers={"User-Agent": _TENCENT_UA, "Referer": "https://gu.qq.com/"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        node: dict[str, Any] = {}
        if isinstance(data, dict):
            node = data.get(tcode) or {}
            if not isinstance(node, dict) or not node:
                node = next((v for v in data.values() if isinstance(v, dict)), {})
        rows = node.get(f"{kind}day") or node.get("day") or []
        if not rows:
            break

        def row6(raw: list) -> list[str]:
            """统一成 6 列（部分行会附带第 7 列的除权信息）。"""
            values = [str(item) for item in list(raw)[:6]]
            return values + [""] * (6 - len(values))

        fresh = [
            row6(row)
            for row in rows
            if str(row[0]) not in seen and start.isoformat() <= str(row[0]) <= end.isoformat()
        ]
        collected = fresh + collected
        seen.update(str(row[0]) for row in rows)
        first = str(rows[0][0])
        if first <= start.isoformat() or len(rows) < 640:
            break
        page_end = date.fromisoformat(first) - timedelta(days=1)

    if not collected:
        raise DataNotFound(f"腾讯没有 {symbol} 在 {start}~{end} 的 K 线")
    frame = pd.DataFrame(collected, columns=["date", "open", "close", "high", "low", "volume"])
    frame["amount"] = 0.0  # 腾讯日线不含成交额
    return normalize_frame(frame, symbol=inst.id)


__all__ = ["AkShareProvider", "sina_daily_cn", "sina_intraday_cn", "tencent_daily"]
