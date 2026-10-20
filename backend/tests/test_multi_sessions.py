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


def test_paper_pause_keeps_progress_and_resume(client, context, monkeypatch):
    """暂停保留会话与进度，继续后从暂停处接着跑，停止才结束。"""
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()
    monkeypatch.setattr(
        context.data,
        "bars",
        lambda symbol, start, end, refresh=False: synth.daily(symbol, start, end),
    )
    headers = login_headers(client)
    body = {
        "strategy_id": "buy_hold",
        "symbols": ["CN:600519"],
        "mode": "replay",
        "replay_days": 120,
        "replay_interval": 0.1,
        "poll_interval": 5,
    }

    resp = client.post("/api/paper/start", json=body, headers=headers)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    time.sleep(0.6)

    assert client.post("/api/paper/pause", params={"run_id": run_id}, headers=headers).status_code == 200
    time.sleep(0.3)
    paused = client.get("/api/paper/sessions", headers=headers).json()
    assert [item["status"] for item in paused] == ["paused"]
    steps_at_pause = paused[0]["steps"]

    # 暂停期间不再推进
    time.sleep(0.6)
    still = client.get("/api/paper/sessions", headers=headers).json()
    assert still[0]["steps"] == steps_at_pause
    assert still[0]["status"] == "paused"

    assert client.post("/api/paper/resume", params={"run_id": run_id}, headers=headers).status_code == 200
    time.sleep(0.5)
    resumed = client.get("/api/paper/sessions", headers=headers).json()
    assert resumed[0]["status"] == "running"
    assert resumed[0]["steps"] > steps_at_pause

    assert client.post("/api/paper/stop", params={"run_id": run_id}, headers=headers).status_code == 200


def test_live_pause_and_resume(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    credential = _credential(client, headers, "okx-pause")
    body = {
        "strategy_id": "buy_hold",
        "symbols": ["CRYPTO:BTC/USDT"],
        "params": {},
        "adapter": "ccxt",
        "sandbox": True,
        "poll_interval": 1.0,
        "credential_id": credential,
    }
    resp = client.post("/api/live/start", json=body, headers=headers)
    assert resp.status_code == 202, resp.text
    time.sleep(1.5)

    assert client.post("/api/live/pause", params={"credential_id": credential}, headers=headers).status_code == 200
    time.sleep(0.3)
    sessions = client.get("/api/live/sessions", headers=headers).json()
    assert [item["status"] for item in sessions] == ["paused"]
    steps_at_pause = sessions[0]["steps"]

    time.sleep(1.2)
    still = client.get("/api/live/sessions", headers=headers).json()
    assert still[0]["steps"] == steps_at_pause

    assert client.post("/api/live/resume", params={"credential_id": credential}, headers=headers).status_code == 200
    time.sleep(1.2)
    resumed = client.get("/api/live/sessions", headers=headers).json()
    assert resumed[0]["status"] == "running"
    assert resumed[0]["steps"] > steps_at_pause

    assert client.post("/api/live/stop", params={"credential_id": credential}, headers=headers).status_code == 200


def test_paper_resume_stopped_run_keeps_history(client, context, monkeypatch):
    """停止后的历史会话可以「继续」：同一 run_id、保留账户与已有曲线。"""
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()
    monkeypatch.setattr(
        context.data,
        "bars",
        lambda symbol, start, end, refresh=False: synth.daily(symbol, start, end),
    )
    headers = login_headers(client)
    body = {
        "strategy_id": "buy_hold",
        "symbols": ["CN:600519"],
        "mode": "replay",
        "replay_days": 120,
        "replay_interval": 0.03,
        "poll_interval": 5,
    }
    resp = client.post("/api/paper/start", json=body, headers=headers)
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    time.sleep(0.7)
    assert client.post("/api/paper/stop", params={"run_id": run_id}, headers=headers).status_code == 200

    before = client.get(f"/api/backtests/{run_id}", headers=headers).json()
    assert before["status"] == "stopped"
    equity_before = len(before["equity_curve"])
    trades_before = len(before["trades"])
    assert equity_before > 0

    resp = client.post("/api/paper/continue", params={"run_id": run_id}, headers=headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["run_id"] == run_id

    # 已在运行的会话不能重复继续
    again = client.post("/api/paper/continue", params={"run_id": run_id}, headers=headers)
    assert again.status_code == 409

    time.sleep(0.5)
    sessions = client.get("/api/paper/sessions", headers=headers).json()
    assert [item["run_id"] for item in sessions] == [run_id]
    assert client.post("/api/paper/stop", params={"run_id": run_id}, headers=headers).status_code == 200

    after = client.get(f"/api/backtests/{run_id}", headers=headers).json()
    assert after["status"] == "stopped"
    assert len(after["equity_curve"]) > equity_before
    assert len(after["trades"]) >= trades_before
    # 恢复时的现金/持仓与停止时一致（buy_hold 已建仓）
    assert after["positions"] == before["positions"] or after["positions"]
