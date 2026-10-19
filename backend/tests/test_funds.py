"""场外基金：申赎聚合、净值数据源、路由契约与用户隔离。"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from quant.core.markets import Market, parse_instrument
from quant.data.providers.fund_provider import FundProvider
from quant.funds import FundPosition, compute_positions

from conftest import login_headers

# --------------------------------------------------------------------------- #
# 聚合纯函数
# --------------------------------------------------------------------------- #


class T:
    """测试用流水（满足 FundTradeLike 协议）。"""

    def __init__(self, symbol, side, shares, amount, fee=0.0, nav=1.0, day=date(2024, 1, 2)):
        self.symbol = symbol
        self.side = side
        self.shares = shares
        self.amount = amount
        self.fee = fee
        self.nav = nav
        self.trade_date = day


def test_subscribe_aggregation():
    summary = compute_positions(
        [T("FUND:007005", "subscribe", shares=1000.0, amount=1000.0, fee=1.5, nav=1.0)]
    )
    pos = summary.positions["FUND:007005"]
    assert pos.shares == pytest.approx(1000.0)
    assert pos.cost == pytest.approx(1000.0)
    assert pos.avg_cost == pytest.approx(1.0)
    assert summary.totals({"FUND:007005": 1.2})["unrealized_pnl"] == pytest.approx(200.0)
    assert summary.totals({"FUND:007005": 1.2})["return_pct"] == pytest.approx(0.2)


def test_weighted_cost_and_redeem_pnl():
    trades = [
        T("FUND:007005", "subscribe", 1000.0, 1000.0, nav=1.0),
        T("FUND:007005", "subscribe", 500.0, 600.0, nav=1.2),
    ]
    pos = compute_positions(trades).positions["FUND:007005"]
    assert pos.shares == pytest.approx(1500.0)
    assert pos.avg_cost == pytest.approx(1600.0 / 1500.0)

    # 赎回 500 份，净值 1.5，到账 750-2.5=747.5（赎回费 2.5）
    trades.append(T("FUND:007005", "redeem", 500.0, 747.5, fee=2.5, nav=1.5))
    pos = compute_positions(trades).positions["FUND:007005"]
    unit_cost = 1600.0 / 1500.0
    assert pos.shares == pytest.approx(1000.0)
    assert pos.cost == pytest.approx(unit_cost * 1000.0)
    assert pos.realized_pnl == pytest.approx(747.5 - unit_cost * 500.0)


def test_redeem_cannot_go_negative():
    trades = [
        T("FUND:007005", "subscribe", 100.0, 100.0, nav=1.0),
        T("FUND:007005", "redeem", 500.0, 700.0, nav=1.4),
    ]
    pos = compute_positions(trades).positions["FUND:007005"]
    assert pos.shares == 0.0
    assert pos.cost == 0.0


def test_position_dict_without_nav_uses_cost():
    pos = FundPosition(symbol="FUND:007005", shares=100.0, cost=120.0)
    payload = pos.to_dict(None)
    assert payload["market_value"] == pytest.approx(120.0)
    assert payload["unrealized_pnl"] == pytest.approx(0.0)
    assert payload["total_pnl"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 数据源（假 akshare）
# --------------------------------------------------------------------------- #


class FakeAk:
    def fund_name_em(self):
        return pd.DataFrame(
            {
                "基金代码": ["007005", "000001"],
                "基金简称": ["华夏中证银行ETF联接A", "华夏成长混合"],
                "基金类型": ["指数型", "混合型"],
            }
        )

    def fund_open_fund_info_em(self, symbol, indicator):
        return pd.DataFrame(
            {
                "净值日期": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "单位净值": [1.0, 1.01, 1.02],
                "日增长率": [0.0, 1.0, 0.99],
            }
        )


@pytest.fixture()
def fund_provider(monkeypatch):
    provider = FundProvider()
    monkeypatch.setattr(FundProvider, "_akshare", lambda self: FakeAk())
    return provider


def test_fund_provider_nav_series(fund_provider):
    points = fund_provider.nav_series("FUND:007005", date(2024, 1, 1), date(2024, 1, 31))
    assert [p["nav"] for p in points] == [1.0, 1.01, 1.02]
    assert fund_provider.latest_nav("FUND:007005") == pytest.approx(1.02)
    assert fund_provider.nav_on("FUND:007005", date(2024, 1, 3)) == pytest.approx(1.01)


def test_fund_provider_daily_maps_to_ohlcv(fund_provider):
    frame = fund_provider.daily("FUND:007005", date(2024, 1, 1), date(2024, 1, 31))
    assert len(frame) == 3
    assert (frame["open"] == frame["close"]).all()
    assert (frame["volume"] == 0).all()
    assert frame["close"].iloc[-1] == pytest.approx(1.02)


def test_fund_provider_search(fund_provider):
    items = fund_provider.search("银行", limit=10)
    assert [i.symbol for i in items] == ["FUND:007005"]
    assert items[0].market == "FUND"
    assert fund_provider.search("", market="CN") == []
    assert len(fund_provider.search("", limit=10)) == 2


def test_fund_market_parsing():
    inst = parse_instrument("FUND:007005")
    assert inst.market is Market.FUND
    assert inst.code == "007005"
    assert parse_instrument("OF:007005").id == "FUND:007005"
    with pytest.raises(ValueError):
        parse_instrument("FUND:ABC")


# --------------------------------------------------------------------------- #
# 路由契约
# --------------------------------------------------------------------------- #


class StubProvider:
    """路由测试用净值源（不联网）。"""

    def fund_list(self):
        return [("007005", "华夏中证银行ETF联接A", "指数型")]

    def search(self, keyword="", limit=50, *, market=None):
        from quant.data.base import SymbolInfo

        return [
            SymbolInfo(
                symbol="FUND:007005",
                name="华夏中证银行ETF联接A",
                board="指数型",
                market="FUND",
            )
        ][:limit]

    def nav_series(self, symbol, start, end):
        return [{"date": "2024-01-02", "nav": 1.0}, {"date": "2024-01-03", "nav": 1.02}]

    def nav_on(self, symbol, day):
        return 1.0 if day < date(2024, 2, 1) else None

    def latest_nav(self, symbol):
        return 1.02


@pytest.fixture()
def stub_fund_router(monkeypatch):
    from quant.server.routers import funds as funds_module

    monkeypatch.setattr(funds_module, "_provider", StubProvider())


@pytest.fixture()
def second_user(client, settings):
    admin = login_headers(client)
    resp = client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "Bob!Passw0rd123", "role": "user"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/auth/login", json={"username": "bob", "password": "Bob!Passw0rd123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_fund_routes_require_fund_market(client, stub_fund_router):
    headers = login_headers(client)
    resp = client.get("/api/funds/nav", params={"symbol": "CN:600519"}, headers=headers)
    assert resp.status_code == 422

    resp = client.get("/api/funds/search", params={"q": "华夏"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["symbol"] == "FUND:007005"


def test_fund_nav_endpoint(client, stub_fund_router):
    headers = login_headers(client)
    resp = client.get(
        "/api/funds/nav",
        params={"symbol": "FUND:007005", "start": "2024-01-01", "end": "2024-01-31"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "FUND:007005"
    assert body["name"] == "华夏中证银行ETF联接A"
    assert [p["nav"] for p in body["points"]] == [1.0, 1.02]


def test_subscribe_redeem_and_holdings(client, stub_fund_router):
    headers = login_headers(client)

    resp = client.post(
        "/api/funds/trades",
        json={
            "symbol": "FUND:007005",
            "side": "subscribe",
            "trade_date": "2024-01-02",
            "amount": 10000.0,
            "nav": 1.0,
            "fee_rate": 0.0015,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    subscribed = resp.json()
    assert subscribed["fee"] == pytest.approx(15.0)
    assert subscribed["shares"] == pytest.approx(9985.0)

    resp = client.get("/api/funds/holdings", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["holdings"][0]["shares"] == pytest.approx(9985.0)
    assert body["holdings"][0]["nav"] == pytest.approx(1.02)
    assert body["totals"]["cost"] == pytest.approx(10000.0)

    # 赎回超过持仓 → 422
    resp = client.post(
        "/api/funds/trades",
        json={"symbol": "FUND:007005", "side": "redeem", "shares": 100000.0, "nav": 1.02},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = client.post(
        "/api/funds/trades",
        json={
            "symbol": "FUND:007005",
            "side": "redeem",
            "shares": 985.0,
            "nav": 1.02,
            "fee_rate": 0.005,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    redeemed = resp.json()
    # 接口按分四舍五入展示，断言允许 0.005 误差
    assert redeemed["fee"] == pytest.approx(985.0 * 1.02 * 0.005, abs=0.005)
    assert redeemed["amount"] == pytest.approx(
        985.0 * 1.02 - 985.0 * 1.02 * 0.005, abs=0.005
    )

    resp = client.get("/api/funds/holdings", headers=headers)
    assert resp.json()["holdings"][0]["shares"] == pytest.approx(9000.0)


def test_trade_isolation_and_delete(client, stub_fund_router, second_user):
    mine = login_headers(client)
    theirs = second_user

    resp = client.post(
        "/api/funds/trades",
        json={"symbol": "FUND:007005", "side": "subscribe", "amount": 500.0, "nav": 1.0},
        headers=mine,
    )
    trade_id = resp.json()["id"]

    # 对方看不到、也删不掉
    resp = client.get("/api/funds/trades", headers=theirs)
    assert resp.json() == []
    resp = client.delete(f"/api/funds/trades/{trade_id}", headers=theirs)
    assert resp.status_code == 404

    # 本人删除成功
    resp = client.delete(f"/api/funds/trades/{trade_id}", headers=mine)
    assert resp.status_code == 204
    resp = client.get("/api/funds/trades", headers=mine)
    assert resp.json() == []


def test_subscribe_requires_amount(client, stub_fund_router):
    headers = login_headers(client)
    resp = client.post(
        "/api/funds/trades",
        json={"symbol": "FUND:007005", "side": "subscribe", "nav": 1.0},
        headers=headers,
    )
    assert resp.status_code == 422


def test_trade_datetime_roundtrip(client, stub_fund_router):
    """ORM 存 DateTime，接口回传 YYYY-MM-DD。"""
    headers = login_headers(client)
    resp = client.post(
        "/api/funds/trades",
        json={"symbol": "FUND:007005", "side": "subscribe", "amount": 100.0, "nav": 1.0},
        headers=headers,
    )
    body = resp.json()
    assert body["trade_date"] == date.today().isoformat()
    assert datetime.fromisoformat(body["created_at"]) is not None
