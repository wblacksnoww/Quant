"""实时行情：多市场解析（A股/美股/港股）、多源聚合回退、TTL 缓存与降级。"""

from __future__ import annotations

import pytest

from quant.core.errors import DataProviderUnavailable
from quant.core.markets import Instrument, Market
from quant.data import realtime as rt
from quant.data.store import MarketDataService


def _line(head: str, values: dict[int, str], size: int = 60) -> str:
    """按 ``v_xxx="..."`` 组装腾讯应答行。"""
    fields = [""] * size
    for index, value in values.items():
        fields[index] = value
    return f'{head}="' + "~".join(fields) + '";'


TENCENT_CN = _line(
    "v_sh600519",
    {
        1: "贵州茅台",
        2: "600519",
        3: "1329.00",
        4: "1312.97",
        5: "1311.57",
        6: "38502",
        30: "20261016161447",
        31: "16.03",
        32: "1.22",
        33: "1329.88",
        34: "1301.68",
        36: "38502",
        37: "507553",
    },
)

TENCENT_US = _line(
    "v_usAAPL",
    {
        1: "苹果",
        2: "AAPL.OQ",
        3: "343.46",
        4: "342.00",
        5: "346.09",
        30: "2026-10-16 16:00:02",
        31: "1.46",
        32: "0.43",
        33: "347.82",
        34: "340.46",
        36: "59946368",
        37: "20629491159",
        46: "Apple Inc",
    },
)

TENCENT_HK = _line(
    "v_hk00700",
    {
        1: "腾讯控股",
        2: "00700",
        3: "417.800",
        4: "417.800",
        5: "424.000",
        30: "2026/10/16 16:08:22",
        31: "-0.000",
        32: "-0.000",
        33: "428.800",
        34: "415.800",
        36: "18752869",
        37: "7901198756.124",
    },
)

SINA_CN = (
    'var hq_str_sh600519="贵州茅台,1311.57,1312.97,1329.00,1329.88,1301.68,'
    "1328.58,1329.00,3850200,5075528306.00,1328.00,100,1327.00,200,1326.00,300,"
    "1325.00,400,1324.00,500,1329.10,100,1330.00,200,1331.00,300,1332.00,400,"
    '1333.00,500,2026-10-16,16:14:47,00";'
)

SINA_US = (
    'var hq_str_gb_aapl="苹果,343.4600,0.43,2026-10-17 07:59:51,1.4600,346.0850,'
    "347.8200,340.4600,348.1500,242.8900,59946368,36020239,5012517063143,8.30,"
    "41.380000,0.00,0.00,0.00,0.00,14594180001,63,342.8120,-0.19,-0.65,"
    "Oct 16 07:59PM EDT,Oct 16 04:00PM EDT,342.0000,7617902,1,2026,"
    '20629491159.0000,343.8499,290.6200,2616346768.8078,343.4600,342.0000";'
)

SINA_HK = (
    'var hq_str_rt_hk00700="TENCENT,腾讯控股,424.000,417.800,428.800,415.800,'
    "417.800,-0.000,-0.000,417.600,417.800,7901198756.124,18752869,15.178,"
    '0.000,656.353,411.000,2026/10/16,16:08:22,100|0,N|Y|Y";'
)

EASTMONEY_PAYLOAD = {
    "rc": 0,
    "data": {
        "total": 1,
        "diff": [
            {
                "f2": 1329.0,
                "f3": 1.22,
                "f4": 16.03,
                "f5": 38502,
                "f6": 5075528306,
                "f12": "600519",
                "f14": "贵州茅台",
                "f15": 1329.88,
                "f16": 1301.68,
                "f17": 1311.57,
                "f18": 1312.97,
            }
        ],
    },
}


def test_exchange_prefix():
    assert rt.exchange_prefix("600519") == "sh"
    assert rt.exchange_prefix("CN:000001") == "sz"
    assert rt.exchange_prefix("300059") == "sz"
    assert rt.exchange_prefix("920930") == "bj"
    assert rt.exchange_prefix("HK:00700") == "hk"
    assert rt.exchange_prefix("us:aapl") == "us"


def test_tencent_code_and_sina_code():
    assert rt.tencent_code(Instrument(Market.CN, "600519")) == "sh600519"
    assert rt.tencent_code(Instrument(Market.HK, "00700")) == "hk00700"
    assert rt.tencent_code(Instrument(Market.US, "AAPL")) == "usAAPL"
    assert rt.sina_code(Instrument(Market.CN, "000001")) == "sz000001"
    assert rt.sina_code(Instrument(Market.HK, "00700")) == "rt_hk00700"
    assert rt.sina_code(Instrument(Market.US, "AAPL")) == "gb_aapl"


def test_parse_tencent_cn():
    quote = rt.parse_tencent(TENCENT_CN)["CN:600519"]
    assert quote.name == "贵州茅台"
    assert quote.market == "CN"
    assert quote.currency == "CNY"
    assert quote.price == pytest.approx(1329.0)
    assert quote.prev_close == pytest.approx(1312.97)
    assert quote.open == pytest.approx(1311.57)
    assert quote.high == pytest.approx(1329.88)
    assert quote.low == pytest.approx(1301.68)
    assert quote.change == pytest.approx(16.03)
    assert quote.change_pct == pytest.approx(1.22)
    assert quote.volume == pytest.approx(38502 * 100)  # 手 → 股
    assert quote.amount == pytest.approx(5075530000)  # 万元 → 元
    assert quote.ts == "2026-10-16 16:14:47"
    assert quote.source == "tencent"


def test_parse_tencent_us():
    quotes = rt.parse_tencent(TENCENT_US)
    quote = quotes["US:AAPL"]
    assert quote.name == "苹果"
    assert quote.market == "US"
    assert quote.currency == "USD"
    assert quote.price == pytest.approx(343.46)
    assert quote.prev_close == pytest.approx(342.0)
    assert quote.volume == pytest.approx(59946368)  # 股，不换算
    assert quote.amount == pytest.approx(20629491159)
    assert quote.ts == "2026-10-16 16:00:02"
    # 交易所后缀顺路进了缓存（K 线接口需要）
    assert rt._US_SUFFIX_CACHE.get("AAPL") == "AAPL.OQ"


def test_parse_tencent_hk():
    quote = rt.parse_tencent(TENCENT_HK)["HK:00700"]
    assert quote.name == "腾讯控股"
    assert quote.market == "HK"
    assert quote.currency == "HKD"
    assert quote.price == pytest.approx(417.8)
    assert quote.volume == pytest.approx(18752869)
    assert quote.amount == pytest.approx(7901198756.124)
    assert quote.ts == "2026-10-16 16:08:22"


def test_parse_sina_cn():
    quote = rt.parse_sina(SINA_CN)["CN:600519"]
    assert quote.name == "贵州茅台"
    assert quote.price == pytest.approx(1329.0)
    assert quote.prev_close == pytest.approx(1312.97)
    assert quote.volume == pytest.approx(3850200)  # 股
    assert quote.amount == pytest.approx(5075528306.0)
    assert quote.change == pytest.approx(16.03)
    assert quote.change_pct == pytest.approx(1.221, rel=1e-3)
    assert quote.source == "sina"


def test_parse_sina_us():
    quote = rt.parse_sina(SINA_US)["US:AAPL"]
    assert quote.name == "苹果"
    assert quote.market == "US"
    assert quote.price == pytest.approx(343.46)
    assert quote.prev_close == pytest.approx(342.0)
    assert quote.open == pytest.approx(346.085)
    assert quote.high == pytest.approx(347.82)
    assert quote.low == pytest.approx(340.46)
    assert quote.volume == pytest.approx(59946368)
    assert quote.amount == pytest.approx(20629491159.0)
    assert quote.change == pytest.approx(1.46)
    assert quote.change_pct == pytest.approx(0.43)
    assert quote.ts == "2026-10-17 07:59:51"


def test_parse_sina_hk():
    quote = rt.parse_sina(SINA_HK)["HK:00700"]
    assert quote.name == "腾讯控股"
    assert quote.market == "HK"
    assert quote.price == pytest.approx(417.8)
    assert quote.prev_close == pytest.approx(417.8)
    assert quote.open == pytest.approx(424.0)
    assert quote.volume == pytest.approx(18752869)
    assert quote.amount == pytest.approx(7901198756.124)
    assert quote.ts == "2026-10-16 16:08:22"


def test_parse_eastmoney():
    quote = rt.parse_eastmoney(EASTMONEY_PAYLOAD)["CN:600519"]
    assert quote.name == "贵州茅台"
    assert quote.price == pytest.approx(1329.0)
    assert quote.volume == pytest.approx(38502 * 100)  # 手 → 股
    assert quote.change_pct == pytest.approx(1.22)
    assert quote.source == "eastmoney"


def test_fetch_quotes_falls_back_to_next_source(monkeypatch):
    def broken(instruments, timeout):
        raise RuntimeError("限流")

    def working(instruments, timeout):
        return {
            inst.id: rt.RealtimeQuote(symbol=inst.id, price=9.9, source="eastmoney")
            for inst in instruments
        }

    monkeypatch.setattr(rt, "_SOURCES", (("腾讯", broken), ("东财", working)))
    quotes = rt.fetch_quotes(["600519", "000001", "AAPL"])
    assert set(quotes) == {"CN:600519", "CN:000001", "US:AAPL"}
    assert quotes["CN:600519"].price == pytest.approx(9.9)


def test_fetch_quotes_all_sources_fail(monkeypatch):
    def broken(instruments, timeout):
        raise RuntimeError("限流")

    monkeypatch.setattr(rt, "_SOURCES", (("腾讯", broken),))
    with pytest.raises(DataProviderUnavailable):
        rt.fetch_quotes(["600519"])


def test_fetch_quotes_skips_invalid_symbol(monkeypatch):
    def working(instruments, timeout):
        return {inst.id: rt.RealtimeQuote(symbol=inst.id, price=1.0) for inst in instruments}

    monkeypatch.setattr(rt, "_SOURCES", (("腾讯", working),))
    quotes = rt.fetch_quotes(["", "not a symbol", "600519"])
    assert set(quotes) == {"CN:600519"}


class _StubProvider:
    name = "stub"
    supports_realtime = True

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def snapshot(self, codes):
        self.calls += 1
        if self.fail:
            raise RuntimeError("数据源挂了")
        return {
            code: rt.RealtimeQuote(symbol=code, price=10.0, name="测试", source="stub")
            for code in codes
        }


def test_service_snapshot_ttl_and_stale_fallback():
    provider = _StubProvider()
    service = MarketDataService(store=None, provider=provider)  # type: ignore[arg-type]

    quotes, note = service.snapshot(["600519"])
    assert quotes["CN:600519"].price == pytest.approx(10.0)
    assert note == ""
    assert provider.calls == 1

    # TTL 内不重复请求
    quotes, _ = service.snapshot(["600519"])
    assert provider.calls == 1

    # ttl=0 强制刷新
    service.snapshot(["600519"], ttl=0)
    assert provider.calls == 2

    # 数据源故障：回退最近一次成功值并标记 stale
    provider.fail = True
    quotes, note = service.snapshot(["600519"], ttl=0)
    assert quotes["CN:600519"].stale is True
    assert quotes["CN:600519"].price == pytest.approx(10.0)
    assert "最近一次" in note

    # 从未成功过的标的：返回空 + 提示
    quotes, note = service.snapshot(["300059"], ttl=0)
    assert quotes == {}
    assert note


def test_service_snapshot_unsupported_provider():
    class _NoRealtime:
        name = "csv"

        def snapshot(self, codes):
            raise NotImplementedError

    service = MarketDataService(store=None, provider=_NoRealtime())  # type: ignore[arg-type]
    quotes, note = service.snapshot(["600519"])
    assert quotes == {}
    assert "不支持" in note


def test_tencent_daily_tolerates_extra_column(monkeypatch):
    """腾讯 K 线个别行附带第 7 列（除权信息），解析必须容错。"""
    from datetime import date

    from quant.data.providers import akshare_provider as ap

    payload = {
        "code": 0,
        "data": {
            "sh600519": {
                "qfqday": [
                    ["2026-10-15", "1300.0", "1310.0", "1315.0", "1295.0", "30000.0"],
                    [
                        "2026-10-16",
                        "1311.57",
                        "1329.0",
                        "1329.88",
                        "1301.68",
                        "38502",
                        {"nd": "1.0"},
                    ],
                ]
            }
        },
    }

    class _FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    monkeypatch.setattr(ap.requests, "get", lambda *a, **k: _FakeResponse(payload))
    frame = ap.tencent_daily("600519", date(2016, 1, 1), date(2026, 10, 18))
    assert len(frame) == 2
    assert float(frame.iloc[-1]["close"]) == pytest.approx(1329.0)
    assert float(frame.iloc[-1]["volume"]) == pytest.approx(38502)  # 手


def test_tencent_daily_us_uses_suffix_code(monkeypatch):
    """美股 K 线：用带交易所后缀的代码请求 usfqkline 端点。"""
    from datetime import date

    from quant.data.providers import akshare_provider as ap

    captured: dict[str, str] = {}

    payload = {
        "code": 0,
        "data": {
            "usAAPL.OQ": {
                "qfqday": [["2026-10-16", "346.09", "343.46", "347.82", "340.46", "59946368"]]
            }
        },
    }

    class _FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    def fake_get(url, params=None, **kwargs):
        captured["url"] = url
        captured["param"] = params["param"]
        return _FakeResponse(payload)

    monkeypatch.setitem(rt._US_SUFFIX_CACHE, "AAPL", "AAPL.OQ")
    monkeypatch.setattr(ap.requests, "get", fake_get)
    frame = ap.tencent_daily("US:AAPL", date(2016, 1, 1), date(2026, 10, 18))
    assert "usfqkline" in captured["url"]
    assert captured["param"].startswith("usAAPL.OQ,")
    assert float(frame.iloc[-1]["volume"]) == pytest.approx(59946368)  # 股
    assert frame["limit_up"].isna().all()  # 美股无涨跌停


def test_quote_endpoint_contract(client):
    from conftest import login_headers

    response = client.get(
        "/api/market/quote", params={"symbols": "600519,000001"}, headers=login_headers(client)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"quotes", "ts", "note"}
    # synthetic 数据源没有真实快照，但契约必须成立
    assert isinstance(body["quotes"], dict)


def test_quote_endpoint_rejects_bad_symbol(client):
    from conftest import login_headers

    response = client.get(
        "/api/market/quote", params={"symbols": "600519,@@@"}, headers=login_headers(client)
    )
    assert response.status_code == 422
