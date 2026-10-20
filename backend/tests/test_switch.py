"""运行中切换策略：会话级替换语义 + 纸盘/实盘路由契约。"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from conftest import login_headers
from test_live_broker import FakeAdapter

from quant.core.clock import SimClock
from quant.core.events import EventBus
from quant.core.portfolio import Account
from quant.core.types import Bar, Order, OrderStatus, OrderType, Side, TimeInForce
from quant.engine.session import TradingSession
from quant.execution.broker import SimBroker
from quant.execution.matching import MatchingEngine
from quant.strategy.base import ParamSpec, Strategy
from quant.strategy.registry import register_strategy


@pytest.fixture()
def live_enabled(settings):
    settings.enable_live_trading = True
    return settings


@pytest.fixture()
def fake_adapter(monkeypatch, context):
    """假适配器 + 本地合成行情（不连网）。"""
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

    monkeypatch.setattr("quant.services.live_service.create_adapter", _factory)
    return _factory


@register_strategy
class SwitchProbe(Strategy):
    """on_start 挂一张远价限价单（不会成交），用于验证切换时撤单。"""

    strategy_id = "switch_probe"
    display_name = "切换探针"
    param_specs = (ParamSpec("price", "float", 1.0, 0.0001, 1e9),)

    def on_start(self):
        self.ctx.submit(
            Order(
                symbol="CN:600519",
                side=Side.BUY,
                quantity=100.0,
                order_type=OrderType.LIMIT,
                limit_price=float(self.params["price"]),
                tif=TimeInForce.GTC,  # 日终不过期，便于验证"切换时撤单"
            )
        )


@register_strategy
class SwitchTarget(Strategy):
    """被切换到的目标策略：记录 on_start 是否被调用。"""

    strategy_id = "switch_target"
    display_name = "切换目标"
    param_specs = ()
    started = 0

    def on_start(self):
        type(self).started += 1


def _bar(day: int = 2) -> Bar:
    return Bar(
        symbol="CN:600519",
        dt=datetime(2024, 1, day, 15, 0),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000_000.0,
    )


def _session() -> TradingSession:
    account = Account(1_000_000, name="switch_test")
    broker = SimBroker(account, EventBus("switch_test"), matching=MatchingEngine())
    session = TradingSession(
        strategy_cls=SwitchProbe,
        strategy_params={"price": 1.0},
        account=account,
        broker=broker,
        risk=None,
        clock=SimClock(_bar().dt.date()),
        bus=EventBus("switch_session"),
        frames={},
    )
    session.begin()
    session.step(_bar(2).dt, {"CN:600519": _bar(2)})
    return session


def test_switch_replaces_strategy_and_cancels_old_orders():
    session = _session()
    broker = session.broker
    assert session.current_strategy_id == "switch_probe"
    old_orders = list(broker.open_orders)
    assert old_orders, "探针策略应挂出一张限价单"

    session.request_switch(SwitchTarget, {}, cancel_orders=True)
    session.step(_bar(3).dt, {"CN:600519": _bar(3)})

    assert session.current_strategy_id == "switch_target"
    assert session.ctx.strategy_name == "switch_target"
    assert all(o.status is OrderStatus.CANCELLED for o in old_orders)
    assert not broker.open_orders
    assert SwitchTarget.started >= 1


def test_switch_keeps_orders_when_disabled():
    session = _session()
    broker = session.broker
    old_orders = list(broker.open_orders)

    session.request_switch(SwitchTarget, {}, cancel_orders=False)
    session.step(_bar(3).dt, {"CN:600519": _bar(3)})

    assert session.current_strategy_id == "switch_target"
    assert all(o.status is OrderStatus.SUBMITTED for o in old_orders)


# --------------------------------------------------------------------------- #
# 路由契约
# --------------------------------------------------------------------------- #


def test_paper_switch_endpoint(client, context, monkeypatch):
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()
    monkeypatch.setattr(
        context.data, "bars", lambda symbol, start, end, refresh=False: synth.daily(symbol, start, end)
    )
    headers = login_headers(client)

    resp = client.post("/api/paper/switch", json={"strategy_id": "switch_target"}, headers=headers)
    assert resp.status_code == 409

    resp = client.post(
        "/api/paper/start",
        json={
            "strategy_id": "switch_probe",
            "symbols": ["CN:600519"],
            "params": {"price": 1.0},
            "mode": "replay",
            "replay_days": 60,
            "replay_interval": 0.2,
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    try:
        time.sleep(1.0)
        resp = client.post(
            "/api/paper/switch",
            json={"strategy_id": "switch_target", "cancel_open_orders": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["detail"]["to"] == "switch_target"
        assert body["detail"]["pending"] is True

        time.sleep(0.8)
        state = client.get("/api/paper/state", headers=headers).json()
        assert state["strategy_id"] == "switch_target"

        bad = client.post("/api/paper/switch", json={"strategy_id": "no_such"}, headers=headers)
        assert bad.status_code == 422
    finally:
        client.post("/api/paper/stop", headers=headers)


def test_live_switch_endpoint(client, live_enabled, fake_adapter):
    headers = login_headers(client)
    resp = client.post(
        "/api/credentials",
        json={
            "name": "okx-switch",
            "kind": "broker",
            "payload": {"exchange": "okx", "api_key": "k", "secret": "s"},
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    credential_id = resp.json()["id"]

    resp = client.post(
        "/api/live/start",
        json={
            "strategy_id": "switch_probe",
            "symbols": ["CRYPTO:BTC/USDT"],
            "params": {"price": 1.0},
            "adapter": "ccxt",
            "credential_id": credential_id,
            "sandbox": True,
            "poll_interval": 1.0,
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    try:
        time.sleep(1.5)
        resp = client.post(
            "/api/live/switch",
            json={"strategy_id": "switch_target"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["detail"]["to"] == "switch_target"

        time.sleep(1.5)
        state = client.get("/api/live/state", headers=headers).json()
        assert state["strategy_id"] == "switch_target"
    finally:
        client.post("/api/live/stop", headers=headers)
