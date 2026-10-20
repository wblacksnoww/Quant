"""排行榜：新浪解析（CN/HK）、美股热门池、虚拟货币批量、服务缓存与路由契约。"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import login_headers

from quant.data.providers import akshare_provider as ap
from quant.data.providers.crypto_provider import CryptoProvider
from quant.data.realtime import RealtimeQuote
from quant.data.store import MarketDataService

# --------------------------------------------------------------------------- #
# 新浪解析
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _cn_rows():
    return [
        {
            "code": "1",
            "name": "A股一",
            "trade": "10.5",
            "changepercent": "3.2",
            "pricechange": "0.33",
            "settlement": "10.17",
            "volume": "1000",
            "amount": "10500",
            "open": "10.2",
            "high": "10.6",
            "low": "10.1",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "trade": "1500",
            "changepercent": "1.1",
            "pricechange": "16",
            "settlement": "1484",
            "volume": "2000",
            "amount": "3000000",
            "open": "1490",
            "high": "1505",
            "low": "1488",
        },
        {
            "code": "300750",
            "name": "宁德时代",
            "trade": "200",
            "changepercent": "-2.5",
            "pricechange": "-5",
            "settlement": "205",
            "volume": "800",
            "amount": "160000",
            "open": "204",
            "high": "205",
            "low": "199",
        },
    ]


def _hk_rows():
    return [
        {
            "symbol": "700",
            "name": "腾讯控股",
            "lasttrade": "417.8",
            "changepercent": "1.5",
            "pricechange": "6.2",
            "prevclose": "411.6",
            "volume": "100000",
            "amount": "41780000",
            "open": "412",
            "high": "418",
            "low": "411",
        },
        {
            "symbol": "9988",
            "name": "阿里巴巴",
            "lasttrade": "80",
            "changepercent": "-1.2",
            "pricechange": "-1",
            "prevclose": "81",
            "volume": "50000",
            "amount": "4000000",
            "open": "81",
            "high": "81.5",
            "low": "79.8",
        },
    ]


def test_sina_ranking_cn_sorted_and_zero_padded(monkeypatch):
    monkeypatch.setattr(ap.requests, "get", lambda *a, **k: _Resp(_cn_rows()))
    items = ap._sina_ranking("CN", "hs_a", sort="change_pct", order="desc", limit=2)
    assert [i.symbol for i in items] == ["CN:000001", "CN:600519"]
    assert items[0].change_pct == pytest.approx(3.2)
    assert items[0].currency == "CNY"
    assert items[0].source == "sina:rank"

    losers = ap._sina_ranking("CN", "hs_a", sort="change_pct", order="asc", limit=1)
    assert losers[0].symbol == "CN:300750"


def test_sina_ranking_hk_and_amount_sort(monkeypatch):
    monkeypatch.setattr(ap.requests, "get", lambda *a, **k: _Resp(_hk_rows()))
    items = ap._sina_ranking("HK", "qbgg_hk", sort="amount", order="desc", limit=5)
    assert [i.symbol for i in items] == ["HK:00700", "HK:09988"]
    assert items[0].currency == "HKD"
    assert items[0].price == pytest.approx(417.8)


def test_sina_ranking_skips_invalid_rows(monkeypatch):
    rows = [
        {"code": "abc", "name": "坏代码", "trade": "10", "changepercent": "1"},
        {"code": "000001", "name": "停牌", "trade": "0", "changepercent": "0"},
        {"code": "000002", "name": "正常", "trade": "5", "changepercent": "2"},
    ]
    monkeypatch.setattr(ap.requests, "get", lambda *a, **k: _Resp(rows))
    items = ap._sina_ranking("CN", "hs_a", sort="change_pct", order="desc", limit=10)
    assert [i.symbol for i in items] == ["CN:000002"]


def test_sina_ranking_network_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(ap.requests, "get", _boom)
    with pytest.raises(Exception, match="排行榜请求失败"):
        ap._sina_ranking("CN", "hs_a", sort="change_pct", order="desc", limit=5)


def test_us_ranking_uses_pool(monkeypatch):
    def _fake_quotes(symbols):
        ids = list(symbols)
        return {
            ids[0]: RealtimeQuote(
                symbol=ids[0],
                name="AAPL",
                price=200.0,
                change_pct=2.0,
                amount=1e9,
                market="US",
                currency="USD",
            ),
            ids[1]: RealtimeQuote(
                symbol=ids[1],
                name="MSFT",
                price=400.0,
                change_pct=-1.0,
                amount=5e8,
                market="US",
                currency="USD",
            ),
        }

    monkeypatch.setattr(ap, "fetch_quotes", _fake_quotes)
    items = ap._us_ranking_quotes()
    ranked = ap._rank_items(items, sort="change_pct", order="desc", limit=1)
    assert ranked[0].symbol == "US:AAPL"


# --------------------------------------------------------------------------- #
# 虚拟货币
# --------------------------------------------------------------------------- #


class FakeCryptoExchange:
    markets = {"BTC/USDT": {}, "ETH/USDT": {}, "SOL/USDT": {}}

    def fetch_tickers(self, pairs):
        return {
            "BTC/USDT": {"last": 80000.0, "previousClose": 79000.0, "percentage": 1.26, "quoteVolume": 5e9},
            "ETH/USDT": {"last": 3000.0, "previousClose": 3100.0, "percentage": -3.22, "quoteVolume": 2e9},
            "SOL/USDT": {"last": 150.0, "previousClose": 149.0, "percentage": 0.67, "quoteVolume": 1e9},
        }


def test_crypto_ranking(monkeypatch):
    provider = CryptoProvider()
    monkeypatch.setattr(CryptoProvider, "_exchange", lambda self: FakeCryptoExchange())
    items = provider.ranking("CRYPTO", sort="change_pct", order="desc", limit=2)
    assert [i.symbol for i in items] == ["CRYPTO:BTC/USDT", "CRYPTO:SOL/USDT"]
    assert items[0].currency == "USDT"
    assert items[0].change_pct == pytest.approx(1.26)

    losers = provider.ranking("CRYPTO", sort="change_pct", order="asc", limit=1)
    assert losers[0].symbol == "CRYPTO:ETH/USDT"

    with pytest.raises(NotImplementedError):
        provider.ranking("CN")


# --------------------------------------------------------------------------- #
# 服务缓存与路由
# --------------------------------------------------------------------------- #


class StubRankProvider:
    name = "stub"
    markets = ("CN",)

    def __init__(self):
        self.calls = 0

    def ranking(self, market, *, sort="change_pct", order="desc", limit=20):
        self.calls += 1
        return [
            RealtimeQuote(
                symbol=f"CN:00000{i}", name=f"票{i}", price=10.0 + i, change_pct=float(i), amount=1e6
            )
            for i in range(1, 6)
        ]


def test_service_ranking_cache_and_limit():
    provider = StubRankProvider()
    service = MarketDataService(store=None, provider=provider)  # type: ignore[arg-type]

    first = service.ranking("CN", sort="change_pct", order="desc", limit=3)
    second = service.ranking("CN", sort="change_pct", order="desc", limit=5)
    assert len(first) == 3 and len(second) == 5
    assert provider.calls == 1  # 60 秒缓存命中

    service.ranking("CN", sort="amount", order="desc", limit=3)
    assert provider.calls == 2  # 不同排序重新取数


def test_ranking_route_contract(client, context, monkeypatch):
    headers = login_headers(client)

    def _fake_ranking(market, *, sort="change_pct", order="desc", limit=20, period="day"):
        return [
            RealtimeQuote(
                symbol="CN:600519",
                name="贵州茅台",
                price=1500.0,
                change_pct=1.1,
                change=16.0,
                amount=3e9,
                market="CN",
                currency="CNY",
            )
        ][:limit]

    monkeypatch.setattr(context.data, "ranking", _fake_ranking)

    resp = client.get(
        "/api/market/ranking",
        params={"market": "CN", "sort": "change_pct", "order": "desc", "limit": 10},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["market"] == "CN"
    assert body["items"][0]["symbol"] == "CN:600519"
    assert body["items"][0]["change_pct"] == pytest.approx(1.1)
    assert body["note"] == ""
    assert datetime.fromisoformat(body["ts"]) is not None

    bad = client.get("/api/market/ranking", params={"market": "XX"}, headers=headers)
    assert bad.status_code == 422

    bad_sort = client.get("/api/market/ranking", params={"sort": "nope"}, headers=headers)
    assert bad_sort.status_code == 422


# --------------------------------------------------------------------------- #
# 本周 / 本月窗口计算
# --------------------------------------------------------------------------- #


def _frame(rows):
    import pandas as pd

    return pd.DataFrame(
        {
            "date": pd.to_datetime([day for day, _ in rows]),
            "close": [close for _, close in rows],
        }
    )


def test_window_change_week_uses_last_close_before_monday():
    rows = [
        ("2024-01-04", 95.0),
        ("2024-01-05", 100.0),  # 上周五收盘 → 本周基准
        ("2024-01-08", 102.0),
        ("2024-01-11", 110.0),  # 周四（最后一根）
    ]
    from quant.data.ranking_windows import window_change

    assert window_change(_frame(rows), "week") == pytest.approx(0.10)


def test_window_change_month_uses_last_close_before_month_start():
    from quant.data.ranking_windows import window_change

    rows = [
        ("2023-12-28", 90.0),
        ("2023-12-29", 100.0),  # 上月末最后一个交易日 → 本月基准
        ("2024-01-02", 101.0),
        ("2024-01-15", 120.0),
    ]
    assert window_change(_frame(rows), "month") == pytest.approx(0.20)


def test_window_change_short_history_falls_back():
    from quant.data.ranking_windows import window_change

    rows = [("2024-01-10", 50.0), ("2024-01-11", 55.0)]
    # 区间内没有更早的收盘（新股），退化为第一根
    assert window_change(_frame(rows), "week") == pytest.approx(0.10)


def test_window_changes_skips_failures():
    from quant.data.ranking_windows import window_changes

    def fetch(symbol, start, end):
        if symbol == "CN:bad":
            raise OSError("boom")
        return _frame([("2024-01-05", 100.0), ("2024-01-11", 110.0)])

    result = window_changes(["CN:good", "CN:bad"], "week", fetch)
    assert result == {"CN:good": pytest.approx(0.10)}


# --------------------------------------------------------------------------- #
# 服务层：period 榜单
# --------------------------------------------------------------------------- #


class WindowStubProvider:
    name = "window_stub"
    markets = ("CN",)

    def ranking(self, market, *, sort="change_pct", order="desc", limit=20):
        return [
            RealtimeQuote(
                symbol=f"CN:00000{i}", name=f"票{i}", price=10.0 + i, change_pct=float(i), amount=1e6 * i
            )
            for i in range(1, 5)
        ]


def test_service_week_ranking_recomputes_and_caches(monkeypatch):
    import pandas as pd

    calls: list[str] = []

    def fake_tencent(symbol, start, end, *, adjust="qfq"):
        calls.append(symbol)
        # 每只票的周涨幅：CN:000001 +1% ... CN:000004 +4%，但故意倒序返回
        base = {"CN:000001": 0.01, "CN:000002": 0.02, "CN:000003": 0.03, "CN:000004": 0.04}[symbol]
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-05", "2024-01-11"]),
                "close": [100.0, 100.0 * (1 + base)],
            }
        )

    monkeypatch.setattr("quant.data.providers.akshare_provider.tencent_daily", fake_tencent)
    provider = WindowStubProvider()
    service = MarketDataService(store=None, provider=provider)  # type: ignore[arg-type]

    items = service.ranking("CN", sort="change_pct", order="desc", period="week", limit=2)
    assert [i.symbol for i in items] == ["CN:000004", "CN:000003"]
    assert items[0].change_pct == pytest.approx(4.0)
    assert len(calls) == 4  # 候选集 4 只各拉一次日线

    # 120 秒缓存：同一 key 不再拉日线
    service.ranking("CN", sort="change_pct", order="desc", period="week", limit=3)
    assert len(calls) == 4


def test_service_day_ranking_short_ttl():
    provider = WindowStubProvider()
    service = MarketDataService(store=None, provider=provider)  # type: ignore[arg-type]
    first = service.ranking("CN", period="day", limit=2)
    second = service.ranking("CN", period="day", limit=2)
    # day 榜信任数据源顺序，不做本地重排
    assert [i.symbol for i in first] == ["CN:000001", "CN:000002"]
    assert [i.symbol for i in second] == [i.symbol for i in first]


def test_ranking_route_period_contract(client, context, monkeypatch):
    headers = login_headers(client)
    captured: dict = {}

    def _fake_ranking(market, *, sort="change_pct", order="desc", limit=20, period="day"):
        captured["period"] = period
        return [
            RealtimeQuote(
                symbol="CN:600519", name="贵州茅台", price=1500.0, change_pct=5.0, market="CN", currency="CNY"
            )
        ]

    monkeypatch.setattr(context.data, "ranking", _fake_ranking)

    resp = client.get(
        "/api/market/ranking",
        params={"market": "CN", "period": "month", "sort": "change_pct"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "month"
    assert captured["period"] == "month"
    assert "本月" in body["note"]

    bad = client.get("/api/market/ranking", params={"period": "year"}, headers=headers)
    assert bad.status_code == 422
