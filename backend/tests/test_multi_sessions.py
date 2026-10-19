"""多会话：模拟盘同账号多开、实盘按凭证并行。"""

from __future__ import annotations

import time

import pytest

from conftest import login_headers
from test_live_broker import FakeAdapter


@pytest.fixture()
def live_enabled(settings):
    settings.enable_live_trading = True
    return settings


@pytest.fixture()
def fake_adapter(monkeypatch, context):
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()
    monkeypatch.setattr(
        context.data,
        "bars",
        lambda symbol, start, end, refresh=False: synth.daily(symbol, start, end),
    )

    def _factory(name: str, *, sandbox: bool = False, **config):
        adapter = FakeAdapter(sandbox=sandbox, **config)
        adapter.cash = 100_000.0
        return adapter

    monkeypatch.setattr("quant.server.routers.live.create_adapter", _factory)
    return _factory


def _paper_body(symbol: str = "CN:600519") -> dict:
    return {
        "strategy_id": "buy_hold",
        "symbols": [symbol],
        "mode": "replay",
        "replay_days": 60,
        "replay_interval": 0.2,
        "poll_interval": 5,
    }


def test_paper_multiple_sessions_same_user(client, context, monkeypatch):
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()
    monkeypatch.setattr(
        context.data,
        "bars",
        lambda symbol, start, end, refresh=False: synth.daily(symbol, start, end),
    )
    headers = login_headers(client)

    run_ids = []
    for symbol in ("CN:600519", "CN:000001"):
        resp = client.post("/api/paper/start", json=_paper_body(symbol), headers=headers)
        assert resp.status_code == 202, resp.text
        run_ids.append(resp.json()["run_id"])

    time.sleep(1.0)
    sessions = client.get("/api/paper/sessions", headers=headers).json()
    assert len(sessions) == 2
    assert {item["run_id"] for item in sessions} == set(run_ids)

    # 指定 run_id 只停一个
    resp = client.post("/api/paper/stop", params={"run_id": run_ids[0]}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["detail"]["stopped"] == 1
    time.sleep(0.5)
    remaining = client.get("/api/paper/sessions", headers=headers).json()
    assert [item["run_id"] for item in remaining] == [run_ids[1]]

    # 不带 run_id 停止全部
    assert client.post("/api/paper/stop", headers=headers).status_code == 200
    assert client.get("/api/paper/sessions", headers=headers).json() == []


def _credential(client, headers, name: str) -> int:
    resp = client.post(
        "/api/credentials",
        json={
            "name": name,
            "kind": "broker",
            "payload": {"exchange": "okx", "api_key": "k", "secret": "s"},
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_live_multiple_credentials_same_user(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    first = _credential(client, headers, "okx-a")
    second = _credential(client, headers, "okx-b")

    body = {
        "strategy_id": "buy_hold",
        "symbols": ["CRYPTO:BTC/USDT"],
        "params": {},
        "adapter": "ccxt",
        "sandbox": True,
        "poll_interval": 1.0,
    }

    resp = client.post("/api/live/start", json={**body, "credential_id": first}, headers=headers)
    assert resp.status_code == 202, resp.text
    try:
        resp = client.post(
            "/api/live/start", json={**body, "credential_id": second}, headers=headers
        )
        assert resp.status_code == 202, resp.text

        # 同一凭证不允许第二个会话
        resp = client.post(
            "/api/live/start", json={**body, "credential_id": first}, headers=headers
        )
        assert resp.status_code == 409

        time.sleep(1.5)
        sessions = client.get("/api/live/sessions", headers=headers).json()
        assert len(sessions) == 2
        assert {item["credential_id"] for item in sessions} == {first, second}

        # 只停一个凭证
        resp = client.post(
            "/api/live/stop", params={"credential_id": first}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["detail"]["stopped"] == 1
        time.sleep(0.5)
        remaining = client.get("/api/live/sessions", headers=headers).json()
        assert [item["credential_id"] for item in remaining] == [second]
    finally:
        client.post("/api/live/stop", headers=headers)
