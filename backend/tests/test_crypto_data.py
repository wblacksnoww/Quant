"""虚拟货币数据源：ccxt 封装（假交易所，不碰网络）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quant.core.errors import DataProviderUnavailable
from quant.data.base import available_providers, create_provider
from quant.data.providers.crypto_provider import CryptoProvider

_DAY_MS = 86_400_000


class FakeExchange:
    """可配置的假 ccxt 交易所：按区间生成 1d K 线。"""

    def __init__(self, total_days: int = 5, max_batch: int = 1000):
        self.total_days = total_days
        self.max_batch = max_batch
        self.calls: list[tuple] = []
        self._origin: int | None = None

    def parse8601(self, text: str) -> int:
        from datetime import datetime

        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)

    def fetch_ohlcv(self, pair, timeframe="1d", since=None, limit=None):
        self.calls.append(("ohlcv", pair, since, limit))
        if self._origin is None:
            self._origin = since or 0
        out = []
        for i in range(self.total_days):
            ts = self._origin + i * _DAY_MS
            if ts < (since or 0):
                continue
            out.append([ts, 100.0 + i, 110.0 + i, 90.0 + i, 105.0 + i, 1000.0 + i])
            if len(out) >= self.max_batch:
                break
        return out

    def fetch_ticker(self, pair):
        self.calls.append(("ticker", pair))
        return {
            "last": 12345.6,
            "open": 12000.0,
            "high": 12500.0,
            "low": 11800.0,
            "baseVolume": 5000.0,
            "quoteVolume": 6.1e7,
            "previousClose": 12200.0,
            "datetime": "2024-01-05T00:00:00Z",
        }


def _provider(monkeypatch, fake) -> CryptoProvider:
    provider = CryptoProvider(exchange="okx")
    monkeypatch.setattr(CryptoProvider, "_exchange", lambda self: fake)
    return provider


def test_provider_registered():
    assert "crypto" in available_providers()
    provider = create_provider("crypto")
    assert isinstance(provider, CryptoProvider)
    assert provider.exchange_id == "okx"


def test_daily_normalize_schema(monkeypatch):
    provider = _provider(monkeypatch, FakeExchange(total_days=5))
    start, end = date(2024, 1, 1), date(2024, 1, 5)
    frame = provider.daily("CRYPTO:BTC/USDT", start, end)

    assert len(frame) == 5
    for col in ("date", "open", "high", "low", "close", "volume", "amount", "prev_close"):
        assert col in frame.columns
    assert (frame["close"] > 0).all()
    assert frame["date"].iloc[0].date() == start
    assert frame["prev_close"].iloc[1] == pytest.approx(frame["close"].iloc[0])
    assert frame["limit_up"].isna().all()  # 无涨跌停
    assert (frame["date"].dt.date >= start).all() and (frame["date"].dt.date <= end).all()


def test_daily_paginates(monkeypatch):
    provider = _provider(monkeypatch, FakeExchange(total_days=1500, max_batch=1000))
    frame = provider.daily("CRYPTO:ETH/USDT", date(2024, 1, 1), date(2028, 12, 31))
    fake = provider._exchange()
    assert len([c for c in fake.calls if c[0] == "ohlcv"]) >= 2
    assert len(frame) == 1500


def test_snapshot_shape(monkeypatch):
    provider = _provider(monkeypatch, FakeExchange())
    quotes = provider.snapshot(["CRYPTO:BTC/USDT"])
    assert "CRYPTO:BTC/USDT" in quotes
    q = quotes["CRYPTO:BTC/USDT"]
    assert q.price == pytest.approx(12345.6)
    assert q.market == "CRYPTO"
    assert q.currency == "USDT"
    assert q.change_pct == pytest.approx((12345.6 / 12200.0 - 1) * 100)


def test_search_whitelist_and_filter(monkeypatch):
    provider = _provider(monkeypatch, FakeExchange())
    all_items = provider.search("", limit=50)
    assert len(all_items) == 20  # 白名单全量
    assert all_items[0].symbol == "CRYPTO:BTC/USDT"
    assert all_items[0].market == "CRYPTO"

    btc = provider.search("btc", limit=5)
    assert [i.symbol for i in btc] == ["CRYPTO:BTC/USDT"]

    cn = provider.search("", market="CN")
    assert cn == []


def test_calendar_full_week(monkeypatch):
    provider = _provider(monkeypatch, FakeExchange())
    days = provider.calendar(date(2024, 1, 5), date(2024, 1, 8))
    assert days == [date(2024, 1, 5), date(2024, 1, 6), date(2024, 1, 7), date(2024, 1, 8)]
    assert date(2024, 1, 6) in days  # 周六
    assert date(2024, 1, 7) in days  # 周日


def test_unknown_exchange_raises(monkeypatch):
    provider = CryptoProvider(exchange="no_such_exchange")
    with pytest.raises(DataProviderUnavailable):
        provider.daily("CRYPTO:BTC/USDT", date(2024, 1, 1), date(2024, 1, 2))


def test_store_cache_name_sanitized(tmp_path):
    """缓存文件名对含 ``/`` 的代码做了转义，落盘路径不产生子目录。

    用纯本地 synthetic 数据源，避免测试触发真实交易所网络。
    """
    from quant.data.providers.synthetic import SyntheticProvider
    from quant.data.store import DataStore

    store = DataStore(root=tmp_path / "store", provider=SyntheticProvider())
    frame = store.get("CRYPTO:BTC/USDT", date(2024, 1, 1), date(2024, 3, 31))
    assert frame is not None and len(frame) > 0
    path = store.path_for("CRYPTO:BTC/USDT")
    assert "/" not in path.name
    assert "~" in path.name
    assert path.exists()
    assert store.has_cache("CRYPTO:BTC/USDT")


def test_provider_routing_by_market(context):
    """数据服务按市场把请求路由到声明的数据源（crypto 不会被当成港股）。"""
    assert context.data._provider_for("CRYPTO").name == "crypto"
    assert context.data._provider_for("CN").name == "synthetic"
    # CRYPTO 标的的降级链把 crypto 排在最前
    assert context.data.store._ordered_sources("CRYPTO")[0].name == "crypto"
    assert context.data.store._ordered_sources("CN")[0].name == "synthetic"

    # 检索同样按市场分派；crypto 搜索是静态白名单，不联网
    items = context.data.search("btc", 5, market="CRYPTO")
    assert [i.symbol for i in items] == ["CRYPTO:BTC/USDT"]

def test_search_all_includes_crypto(context):
    """不限市场的搜索要能搜到虚拟货币（之前只查股票主源）。"""
    items = context.data.search("btc", 10)
    assert any(item.symbol == "CRYPTO:BTC/USDT" for item in items)


def test_search_all_matches_crypto_name(context):
    items = context.data.search("比特币", 10)
    assert any(item.symbol == "CRYPTO:BTC/USDT" for item in items)


def test_crypto_search_accepts_market_prefix(monkeypatch):
    from quant.data.providers.crypto_provider import CryptoProvider

    provider = CryptoProvider()
    monkeypatch.setattr(CryptoProvider, "_exchange", lambda self: None)
    assert [i.symbol for i in provider.search("CRYPTO:BTC", 5)] == ["CRYPTO:BTC/USDT"]
    assert [i.symbol for i in provider.search("cc:eth", 5)] == ["CRYPTO:ETH/USDT"]
    assert [i.symbol for i in provider.search("BTC/USDT", 5)] == ["CRYPTO:BTC/USDT"]
