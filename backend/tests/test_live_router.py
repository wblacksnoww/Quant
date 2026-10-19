"""实盘路由：三重闸门、凭据归属、单会话、启停与审计。"""

from __future__ import annotations

import pytest

from conftest import login_headers
from test_live_broker import FakeAdapter


@pytest.fixture()
def live_enabled(settings):
    settings.enable_live_trading = True
    return settings


@pytest.fixture()
def fake_adapter(monkeypatch, context):
    """把路由的适配器工厂替换成假适配器（不连网），并让行情走本地合成数据。"""

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


def _broker_credential(client, headers, *, kind: str = "broker") -> int:
    resp = client.post(
        "/api/credentials",
        json={
            "name": "okx-test",
            "kind": kind,
            "payload": {"exchange": "okx", "api_key": "k", "secret": "s"},
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


LIVE_BODY = {
    "strategy_id": "buy_hold",
    "symbols": ["CRYPTO:BTC/USDT"],
    "adapter": "ccxt",
    "sandbox": True,
    "poll_interval": 1.0,
}


def test_adapters_endpoint(client):
    headers = login_headers(client)
    resp = client.get("/api/live/adapters", headers=headers)
    assert resp.status_code == 200, resp.text
    names = {item["name"] for item in resp.json()}
    assert names == {"ccxt", "miniqmt"}
    ccxt = next(item for item in resp.json() if item["name"] == "ccxt")
    assert ccxt["supports_sandbox"] is True
    assert ccxt["markets"] == ["CRYPTO"]


def test_live_disabled_by_default(client):
    headers = login_headers(client)
    credential_id = _broker_credential(client, headers)
    resp = client.post(
        "/api/live/start",
        json={**LIVE_BODY, "credential_id": credential_id},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "QUANT_ENABLE_LIVE_TRADING" in resp.json()["detail"]


def test_live_requires_owned_broker_credential(client, live_enabled, fake_adapter):
    headers = login_headers(client)

    resp = client.post("/api/live/start", json={**LIVE_BODY, "credential_id": 99999}, headers=headers)
    assert resp.status_code == 404

    datasource_id = _broker_credential(client, headers, kind="datasource")
    resp = client.post(
        "/api/live/start",
        json={**LIVE_BODY, "credential_id": datasource_id},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "broker" in resp.json()["detail"]


def test_live_real_money_requires_confirmation(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    credential_id = _broker_credential(client, headers)
    resp = client.post(
        "/api/live/start",
        json={**LIVE_BODY, "credential_id": credential_id, "sandbox": False},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "confirm_live" in resp.json()["detail"]


def test_live_start_stop_single_session_and_audit(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    credential_id = _broker_credential(client, headers)

    resp = client.post(
        "/api/live/start",
        json={**LIVE_BODY, "credential_id": credential_id},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"
    assert resp.json()["mode"] == "sandbox"

    try:
        # 同一账号不允许并发
        resp = client.post(
            "/api/live/start",
            json={**LIVE_BODY, "credential_id": credential_id},
            headers=headers,
        )
        assert resp.status_code == 409

        resp = client.get("/api/live/state", headers=headers)
        assert resp.status_code == 200
        state = resp.json()
        assert state is not None
        assert state["mode"] == "live"
        assert state["adapter"] == "fake"  # 路由已把工厂换成假适配器
        assert state["sandbox"] is True
    finally:
        resp = client.post("/api/live/stop", headers=headers)
        assert resp.status_code == 200, resp.text

    resp = client.post("/api/live/stop", headers=headers)
    assert resp.status_code == 409  # 已无运行中会话

    audit = client.get("/api/system/audit", params={"action": "live.start"}, headers=headers)
    assert audit.status_code == 200
    assert any(item["action"] == "live.start" for item in audit.json())


def test_live_state_is_per_user(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    credential_id = _broker_credential(client, headers)

    admin = client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "Bob!Passw0rd123", "role": "user"},
        headers=headers,
    )
    assert admin.status_code == 201, admin.text
    login = client.post("/api/auth/login", json={"username": "bob", "password": "Bob!Passw0rd123"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(
        "/api/live/start",
        json={**LIVE_BODY, "credential_id": credential_id},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    try:
        assert client.get("/api/live/state", headers=other).json() is None
        # 别人的凭据不能被引用
        resp = client.post(
            "/api/live/start",
            json={**LIVE_BODY, "credential_id": credential_id},
            headers=other,
        )
        assert resp.status_code in (403, 404)
    finally:
        client.post("/api/live/stop", headers=headers)
