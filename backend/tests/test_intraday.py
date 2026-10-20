"""分钟 K 线：新浪解析、虚拟货币、路由契约与不支持的市场。"""

from __future__ import annotations

import pandas as pd
import pytest
from conftest import login_headers
from test_crypto_data import FakeExchange

from quant.data.providers import akshare_provider as ap
from quant.data.providers.crypto_provider import CryptoProvider


class _Resp:
    text = (
        '/*<script>location.href="//sina.com";</script>*/\n'
        'var _=([{"day":"2026-10-19 14:50:00","open":"1324.500","high":"1326.880",'
        '"low":"1324.360","close":"1325.390","volume":"58400","amount":"77411602.5"},'
        '{"day":"2026-10-19 14:55:00","open":"1325.070","high":"1327.100",'
        '"low":"1323.230","close":"1324.970","volume":"41000","amount":"54300000"}]);'
    )

    def raise_for_status(self):
        return None


def test_sina_intraday_parsing(monkeypatch):
    monkeypatch.setattr(ap.requests, "get", lambda *a, **k: _Resp())
    frame = ap.sina_intraday_cn("CN:600519", freq="5m", limit=10)
    assert len(frame) == 2
    assert frame["close"].iloc[-1] == pytest.approx(1324.97)
    assert frame["amount"].iloc[0] == pytest.approx(77411602.5)
    # 时间保留到分钟
    assert frame["date"].iloc[0].strftime("%Y-%m-%d %H:%M") == "2026-10-19 14:50"


def test_sina_intraday_rejects_non_cn():
    with pytest.raises(Exception, match="只实现了 A 股"):
        ap.sina_intraday_cn("HK:00700", freq="5m", limit=10)


def test_crypto_intraday(monkeypatch):
    provider = CryptoProvider()
    monkeypatch.setattr(CryptoProvider, "_exchange", lambda self: FakeExchange(total_days=3, max_batch=1000))
    frame = provider.intraday("CRYPTO:BTC/USDT", freq="5m", limit=3)
    assert len(frame) == 3
    assert (frame["close"] > 0).all()


def test_intraday_route_contract(client, context, monkeypatch):
    def fake_intraday(symbol, *, freq="5m", limit=240):
        if not symbol.startswith("CN:"):
            raise NotImplementedError("暂不支持该市场")
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-10-19 14:50", "2026-10-19 14:55"]),
                "open": [1.0, 2.0],
                "high": [2.0, 3.0],
                "low": [0.5, 1.5],
                "close": [1.5, 2.5],
                "volume": [100.0, 200.0],
                "amount": [150.0, 500.0],
            }
        )

    monkeypatch.setattr(context.data, "intraday", fake_intraday)
    headers = login_headers(client)

    resp = client.get(
        "/api/market/intraday",
        params={"symbol": "CN:600519", "freq": "5m", "limit": 100},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "CN:600519"
    assert body["bars"][0]["date"] == "2026-10-19 14:50"
    assert body["bars"][1]["close"] == pytest.approx(2.5)
    assert body["source"].endswith(":5m")

    bad_freq = client.get(
        "/api/market/intraday", params={"symbol": "CN:600519", "freq": "7m"}, headers=headers
    )
    assert bad_freq.status_code == 422

    # 港股暂不支持分钟线 → 422（不联网）
    hk = client.get("/api/market/intraday", params={"symbol": "HK:00700"}, headers=headers)
    assert hk.status_code == 422
    assert "不支持" in hk.json()["detail"]
