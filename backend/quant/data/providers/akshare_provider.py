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
import time
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd
import requests

from ...core.errors import DataNotFound, DataProviderUnavailable
from ...core.markets import Market, parse_instrument
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
    """

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
                frame = _tencent_daily(inst.id, start, end, adjust=adjust)
            except Exception as backup:  # noqa: BLE001
                raise DataProviderUnavailable(
                    f"{inst.id} 日线获取失败：akshare({primary})；腾讯({backup})"
                ) from backup

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
                frame = _tencent_daily(inst.id, start, end, adjust=adjust)
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


def _tencent_daily(symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
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


__all__ = ["AkShareProvider"]
