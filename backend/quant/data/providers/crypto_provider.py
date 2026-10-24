"""虚拟货币数据源：ccxt 统一封装，交易所可配置。

* 交易所：``QUANT_CRYPTO_EXCHANGE``（默认 ``okx``，binance/gate 等 ccxt 支持的皆可）；
* 代理：``QUANT_CRYPTO_PROXY``（可选，如 ``http://127.0.0.1:10808``，直连不畅时用）；
* 无需 API Key（公开行情接口）。

K 线以 UTC 自然日为边界（交易所 1d 口径），7×24 无休市。
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime

import pandas as pd

from ...core.errors import DataError, DataNotFound, DataProviderUnavailable
from ...core.markets import Market, parse_instrument
from ..base import DataProvider, SymbolInfo, normalize_frame, register_provider
from ..calendar import calendar_range
from ..realtime import RealtimeQuote

log = logging.getLogger(__name__)

# 主流 USDT 交易对白名单（ccxt 无免费的全局检索接口，用静态清单兜底）
_TOP_PAIRS: list[tuple[str, str]] = [
    ("BTC/USDT", "比特币"),
    ("ETH/USDT", "以太坊"),
    ("SOL/USDT", "Solana"),
    ("BNB/USDT", "BNB"),
    ("XRP/USDT", "瑞波币"),
    ("DOGE/USDT", "狗狗币"),
    ("ADA/USDT", "艾达币"),
    ("TRX/USDT", "波场"),
    ("LTC/USDT", "莱特币"),
    ("AVAX/USDT", "雪崩"),
    ("LINK/USDT", "Chainlink"),
    ("DOT/USDT", "波卡"),
    ("SHIB/USDT", "柴犬币"),
    ("TON/USDT", "TON"),
    ("UNI/USDT", "Uniswap"),
    ("BCH/USDT", "比特币现金"),
    ("NEAR/USDT", "NEAR"),
    ("APT/USDT", "Aptos"),
    ("ARB/USDT", "Arbitrum"),
    ("OP/USDT", "Optimism"),
]


_TIMEFRAMES = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "1h"}


def _env(key: str, default: str) -> str:
    return (os.environ.get(key) or "").strip() or default


@register_provider("crypto")
class CryptoProvider(DataProvider):
    """ccxt 虚拟货币行情源。

    实例化不联网，首次取数时才建立交易所连接（惰性失败，便于多源降级）。
    """

    name = "crypto"
    markets = ("CRYPTO",)
    supports_realtime = True
    supports_search = True

    def __init__(self, exchange: str | None = None, proxy: str | None = None) -> None:
        self.exchange_id = (exchange or _env("QUANT_CRYPTO_EXCHANGE", "okx")).strip().lower()
        self.proxy = proxy or _env("QUANT_CRYPTO_PROXY", "")
        self._ex = None

    # ------------------------------------------------------------------ #
    def _exchange(self):
        """惰性构造 ccxt 交易所实例。"""
        if self._ex is None:
            try:
                import ccxt  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - 依赖安装问题
                raise DataProviderUnavailable(
                    "未安装 ccxt：pip install ccxt（或安装 quant[crypto-exchange]）"
                ) from exc
            if not hasattr(ccxt, self.exchange_id):
                raise DataProviderUnavailable(f"ccxt 不支持的交易所: {self.exchange_id}")
            config: dict = {"enableRateLimit": True}
            if self.proxy:
                config["proxies"] = {"http": self.proxy, "https": self.proxy}
            self._ex = getattr(ccxt, self.exchange_id)(config)
        return self._ex

    # ------------------------------------------------------------------ #
    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        inst = parse_instrument(symbol)
        pair = inst.code
        ex = self._exchange()
        since = ex.parse8601(f"{start.isoformat()}T00:00:00Z")
        end_ms = ex.parse8601(f"{end.isoformat()}T23:59:59Z")
        if not since or not end_ms:
            raise DataProviderUnavailable(f"日期区间无法解析: {start}~{end}")

        candles: list[list] = []
        cursor = since
        try:
            while cursor <= end_ms:
                batch = ex.fetch_ohlcv(pair, timeframe="1d", since=cursor, limit=1000)
                if not batch:
                    break
                candles.extend(batch)
                last_ts = int(batch[-1][0])
                if last_ts <= cursor:
                    break
                cursor = last_ts + 1
                # 交易所单次返回条数有上限（OKX 约 300），不能按 1000 判断是否结束；
                # 覆盖到 end 就停，否则继续向后翻页
                if last_ts >= end_ms:
                    break
        except DataProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/限流统一转领域错误
            raise DataProviderUnavailable(f"{self.exchange_id} 拉取 {pair} K 线失败: {exc}") from exc

        if not candles:
            raise DataNotFound(f"{pair} 无 K 线数据（{self.exchange_id}）")

        df = pd.DataFrame(
            [row[:6] for row in candles],
            columns=["date", "open", "high", "low", "close", "volume"],
        )
        # ccxt 时间戳是 UTC 毫秒；1d bar 的时间戳即 UTC 当日 0 点
        df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
        df["amount"] = df["close"] * df["volume"]
        out = normalize_frame(df, symbol=inst.id)
        out = out[(out["date"].dt.date >= start) & (out["date"].dt.date <= end)]
        if len(out) == 0:
            raise DataNotFound(f"{pair} 区间 {start}~{end} 无数据")
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def snapshot(self, symbols) -> dict[str, RealtimeQuote]:
        ex = self._exchange()
        out: dict[str, RealtimeQuote] = {}
        for symbol in symbols:
            inst = parse_instrument(symbol)
            pair = inst.code
            try:
                ticker = ex.fetch_ticker(pair)
            except Exception as exc:  # noqa: BLE001 - 单标的失败跳过，全部失败才报错
                log.warning("快照 %s 失败: %s", pair, exc)
                continue
            price = float(ticker.get("last") or ticker.get("close") or 0.0)
            if price <= 0:
                continue
            prev = float(ticker.get("previousClose") or ticker.get("open") or 0.0) or None
            out[inst.id] = RealtimeQuote(
                symbol=inst.id,
                name=pair,
                price=price,
                prev_close=prev,
                open=float(ticker.get("open") or 0.0) or None,
                high=float(ticker.get("high") or 0.0) or None,
                low=float(ticker.get("low") or 0.0) or None,
                volume=float(ticker.get("baseVolume") or ticker.get("volume") or 0.0) or None,
                amount=float(ticker.get("quoteVolume") or 0.0) or None,
                change=price - prev if prev else None,
                change_pct=(price / prev - 1.0) * 100.0 if prev else None,
                ts=str(ticker.get("datetime") or ""),
                source=f"crypto:{self.exchange_id}",
                market="CRYPTO",
                currency="USDT",
            )
        if not out:
            raise DataProviderUnavailable(f"{self.exchange_id} 实时行情不可用")
        return out

    # ------------------------------------------------------------------ #
    def search(self, keyword: str = "", limit: int = 50, *, market: str | None = None) -> list[SymbolInfo]:
        wanted = (market or "").strip().upper()
        if wanted and wanted not in ("CRYPTO", "ALL", "全部"):
            return []
        kw = keyword.strip().upper()
        # 允许用户直接输入 CRYPTO:BTC / CC:BTC（省略计价币时按 USDT 匹配）
        for prefix in ("CRYPTO:", "CC:", "CRYPTO.", "CC."):
            if kw.startswith(prefix):
                kw = kw[len(prefix) :]
                break
        kw = kw.strip()
        out: list[SymbolInfo] = []
        for code, name in _TOP_PAIRS:
            base = code.split("/")[0]
            if kw and kw not in code and kw not in name.upper() and kw != base:
                continue
            out.append(
                SymbolInfo(
                    symbol=f"CRYPTO:{code}",
                    name=f"{name} {code}",
                    board="虚拟货币",
                    market="CRYPTO",
                    kind="other",
                )
            )
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ #
    def ranking(
        self,
        market: str,
        *,
        sort: str = "change_pct",
        order: str = "desc",
        limit: int = 20,
    ) -> list[RealtimeQuote]:
        """主流交易对排行榜（批量 tickers，一次请求）。"""
        if str(market or "").strip().upper() != "CRYPTO":
            raise NotImplementedError("crypto 只支持 CRYPTO 市场的排行榜")
        ex = self._exchange()
        names = dict(_TOP_PAIRS)
        # 过滤交易所没有的交易对（如 OKX 无 TON/USDT），避免一个无效 pair 拖垮批量请求
        available = getattr(ex, "markets", None) or {}
        pairs = [pair for pair in names if not available or pair in available] or list(names)
        tickers: dict = {}
        try:
            tickers = ex.fetch_tickers(pairs)
        except Exception as exc:  # noqa: BLE001 - 批量失败时逐个兜底
            log.info("批量行情失败，逐个拉取: %s", exc)
            for pair in pairs:
                try:
                    tickers[pair] = ex.fetch_ticker(pair)
                except Exception:  # noqa: BLE001
                    continue

        out: list[RealtimeQuote] = []
        for pair, ticker in (tickers or {}).items():
            if pair not in names or not isinstance(ticker, dict):
                continue
            price = float(ticker.get("last") or ticker.get("close") or 0.0)
            if price <= 0:
                continue
            prev = float(ticker.get("previousClose") or ticker.get("open") or 0.0) or None
            pct = ticker.get("percentage")
            if pct is not None:
                change_pct = float(pct)
            elif prev:
                change_pct = (price / prev - 1.0) * 100.0
            else:
                change_pct = None
            out.append(
                RealtimeQuote(
                    symbol=f"CRYPTO:{pair}",
                    name=names[pair],
                    price=price,
                    prev_close=prev,
                    open=float(ticker.get("open") or 0.0) or None,
                    high=float(ticker.get("high") or 0.0) or None,
                    low=float(ticker.get("low") or 0.0) or None,
                    volume=float(ticker.get("baseVolume") or 0.0) or None,
                    amount=float(ticker.get("quoteVolume") or 0.0) or None,
                    change=(price - prev) if prev else None,
                    change_pct=change_pct,
                    ts=str(ticker.get("datetime") or ""),
                    source=f"crypto:{self.exchange_id}",
                    market="CRYPTO",
                    currency="USDT",
                )
            )

        if sort == "amount":
            key_fn = lambda q: q.amount or 0.0  # noqa: E731
        elif sort == "change":
            key_fn = lambda q: q.change or 0.0  # noqa: E731
        else:
            key_fn = lambda q: q.change_pct or 0.0  # noqa: E731
        out.sort(key=key_fn, reverse=str(order).lower() != "asc")
        return out[:limit]

    # ------------------------------------------------------------------ #
    # 分钟 K 线（今日/本周视图）
    # ------------------------------------------------------------------ #
    def intraday(self, symbol: str, *, freq: str = "5m", limit: int = 240) -> pd.DataFrame:
        inst = parse_instrument(symbol)
        if inst.market is not Market.CRYPTO:
            raise NotImplementedError("crypto 适配器只支持 CRYPTO 市场")
        ex = self._exchange()
        timeframe = _TIMEFRAMES.get(freq, "5m")
        try:
            candles = ex.fetch_ohlcv(inst.code, timeframe=timeframe, limit=max(10, min(1000, limit)))
        except Exception as exc:  # noqa: BLE001
            raise DataProviderUnavailable(f"{self.exchange_id} 分钟线 {inst.code} 失败: {exc}") from exc
        if not candles:
            raise DataNotFound(f"{inst.code} 无分钟线数据")
        frame = pd.DataFrame(
            [row[:6] for row in candles],
            columns=["date", "open", "high", "low", "close", "volume"],
        )
        frame["date"] = pd.to_datetime(frame["date"], unit="ms", utc=True).dt.tz_localize(None)
        frame["amount"] = frame["close"] * frame["volume"]
        return normalize_frame(frame, symbol=inst.id)

    # ------------------------------------------------------------------ #
    def intraday_range(
        self, symbol: str, start: datetime, end: datetime, *, freq: str = "1m"
    ) -> pd.DataFrame:
        """按时间区间分页拉取分钟 K 线（交易所单次有上限）。

        ``date`` 列为 bar 的**开始时间**（naive UTC）；频率只支持 _TIMEFRAMES 键。
        """
        inst = parse_instrument(symbol)
        timeframe = _TIMEFRAMES.get(freq)
        if timeframe is None:
            raise DataError(f"不支持的分钟周期: {freq}")
        ex = self._exchange()
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        if since_ms >= end_ms:
            raise DataError(f"分钟线区间无效: {start} ~ {end}")
        if end_ms - since_ms > 90 * 24 * 3600 * 1000:
            raise DataError("分钟线一次最多拉取 90 天，请缩小区间")

        rows: list[list] = []
        cursor = since_ms
        try:
            while cursor < end_ms:
                batch = ex.fetch_ohlcv(inst.code, timeframe=timeframe, since=cursor, limit=300)
                if not batch:
                    break
                rows.extend(r[:6] for r in batch)
                last_ts = int(batch[-1][0])
                if last_ts <= cursor:
                    break
                cursor = last_ts + 1
                if last_ts >= end_ms:
                    break
        except DataProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DataProviderUnavailable(f"{self.exchange_id} 分钟线 {inst.code} 拉取失败: {exc}") from exc

        if not rows:
            raise DataNotFound(f"{inst.code} 区间 {start}~{end} 无 {freq} 分钟线")
        frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        frame["date"] = pd.to_datetime(frame["date"], unit="ms", utc=True).dt.tz_localize(None)
        frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] < pd.Timestamp(end))]
        frame["amount"] = frame["close"] * frame["volume"]
        frame = frame.drop_duplicates(subset="date").sort_values("date")
        if frame.empty:
            raise DataNotFound(f"{inst.code} 区间 {start}~{end} 无 {freq} 分钟线")
        return normalize_frame(frame.reset_index(drop=True), symbol=inst.id)

    # ------------------------------------------------------------------ #
    def calendar(self, start: date, end: date) -> list[date]:
        """虚拟货币 7×24 无休市，全周日历。"""
        return calendar_range(start, end)


__all__ = ["CryptoProvider"]
