"""K 线周期：周/月聚合与任意周期路由契约。"""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import login_headers
from quant.data.aggregate import resample_ohlcv


def _daily(rows):
    return pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows]),
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [100.0 for _ in rows],
            "amount": [1000.0 for _ in rows],
        }
    )


ROWS = [
    ("2024-01-01", 10, 12, 9, 11),
    ("2024-01-02", 11, 13, 10, 12),
    ("2024-01-05", 12, 14, 11, 13),
    ("2024-01-08", 13, 15, 12, 14),
    ("2024-01-09", 14, 16, 13, 15),
]


def test_weekly_aggregation():
    out = resample_ohlcv(_daily(ROWS), "1w")
    assert len(out) == 2
    first, second = out.iloc[0], out.iloc[1]
    assert first["open"] == pytest.approx(10)
    assert first["high"] == pytest.approx(14)
    assert first["low"] == pytest.approx(9)
    assert first["close"] == pytest.approx(13)
    assert first["volume"] == pytest.approx(300)
    assert second["open"] == pytest.approx(13)
    assert second["close"] == pytest.approx(15)
    assert second["prev_close"] == pytest.approx(13)


def test_monthly_aggregation():
    out = resample_ohlcv(_daily(ROWS), "1M")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == pytest.approx(10)
    assert row["high"] == pytest.approx(16)
    assert row["low"] == pytest.approx(9)
    assert row["close"] == pytest.approx(15)
    assert row["volume"] == pytest.approx(500)


def test_resample_passthrough_for_other_intervals():
    frame = _daily(ROWS)
    assert resample_ohlcv(frame, "1d") is frame


def test_klines_route_contract(client, context, monkeypatch):
    def fake_klines(symbol, *, interval="1d", limit=240):
        return _daily(ROWS).tail(limit)

    monkeypatch.setattr(context.data, "klines", fake_klines)
    headers = login_headers(client)

    resp = client.get(
        "/api/market/klines",
        params={"symbol": "CN:600519", "interval": "1w", "limit": 10},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["bars"]) == 5
    assert body["bars"][0]["date"] == "2024-01-01"
    assert body["source"].endswith(":1w")

    bad = client.get(
        "/api/market/klines", params={"symbol": "CN:600519", "interval": "2d"}, headers=headers
    )
    assert bad.status_code == 422

    def boom(symbol, *, interval="1d", limit=240):
        raise NotImplementedError("no minute")

    monkeypatch.setattr(context.data, "klines", boom)
    unsupported = client.get(
        "/api/market/klines", params={"symbol": "US:AAPL", "interval": "5m"}, headers=headers
    )
    assert unsupported.status_code == 422
    assert "不支持" in unsupported.json()["detail"]
