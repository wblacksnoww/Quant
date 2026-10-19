"""实盘适配器：ccxt（假模块）与 miniQMT（假 xtquant）的接口与状态映射。"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from quant.core.types import Order, OrderType, Side
from quant.execution.adapters.base import AdapterError
from quant.execution.adapters.ccxt_adapter import CcxtAdapter
from quant.execution.adapters.miniqmt_adapter import MiniQmtAdapter, _xt_code


def _order(symbol="CRYPTO:BTC/USDT", side=Side.BUY, qty=0.1, limit=None) -> Order:
    return Order(
        symbol=symbol,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET if limit is None else OrderType.LIMIT,
        limit_price=limit,
    )


# --------------------------------------------------------------------------- #
# ccxt
# --------------------------------------------------------------------------- #


class FakeCcxtExchange:
    def __init__(self, config):
        self.config = config
        self.sandbox_calls: list[bool] = []
        self.created: list[tuple] = []
        self.cancelled: list[tuple] = []
        self.orders: dict[str, dict] = {}

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox_calls.append(enabled)

    def load_markets(self):
        return {"BTC/USDT": {}}

    def create_order(self, pair, order_type, side, amount, price=None):
        self.created.append((pair, order_type, side, amount, price))
        ext = f"ccxt-{len(self.created)}"
        self.orders[ext] = {
            "status": "open",
            "filled": 0.0,
            "average": 0.0,
            "fee": {"cost": 0.0},
            "timestamp": 1_700_000_000_000,
        }
        return {"id": ext}

    def cancel_order(self, external_id, pair):
        self.cancelled.append((external_id, pair))
        self.orders[external_id]["status"] = "canceled"
        return {"id": external_id}

    def fetch_balance(self):
        return {"USDT": {"free": 900.0, "total": 1000.0}, "total": {"USDT": 1000.0}, "free": {"USDT": 900.0}}

    def fetch_ticker(self, pair):
        return {"last": 60_000.0 + len(pair)}

    def fetch_order(self, external_id, pair):
        return self.orders[external_id]


@pytest.fixture()
def fake_ccxt(monkeypatch):
    exchange_holder: dict[str, FakeCcxtExchange] = {}

    class _Factory(FakeCcxtExchange):
        def __init__(self, config):
            super().__init__(config)
            exchange_holder["instance"] = self

    module = types.ModuleType("ccxt")
    module.okx = _Factory
    module.binance = _Factory
    monkeypatch.setitem(sys.modules, "ccxt", module)
    return exchange_holder


def test_ccxt_requires_api_key(fake_ccxt):
    adapter = CcxtAdapter(api_key="", secret="")
    with pytest.raises(AdapterError, match="API Key"):
        adapter.connect()


def test_ccxt_connect_sandbox_and_place_order(fake_ccxt):
    adapter = CcxtAdapter(sandbox=True, exchange="okx", api_key="k", secret="s")
    adapter.connect()
    ex = fake_ccxt["instance"]
    assert ex.sandbox_calls == [True]
    assert ex.config["options"]["defaultType"] == "spot"

    ext = adapter.place_order(_order(qty=0.01, limit=50_000.0))
    assert ext == "ccxt-1"
    pair, order_type, side, amount, price = ex.created[0]
    assert pair == "BTC/USDT"
    assert order_type == "limit"
    assert side == "buy"
    assert amount == pytest.approx(0.01)
    assert price == pytest.approx(50_000.0)


def test_ccxt_sync_reports_fill_and_terminal_status(fake_ccxt):
    adapter = CcxtAdapter(sandbox=True, api_key="k", secret="s")
    adapter.connect()
    ext = adapter.place_order(_order())
    ex = fake_ccxt["instance"]
    ex.orders[ext].update(status="closed", filled=0.1, average=60_000.0, fee={"cost": 6.0})

    events = adapter.sync()
    fills = [e for e in events if hasattr(e, "filled")]
    statuses = [e for e in events if hasattr(e, "status")]
    assert fills[0].filled == pytest.approx(0.1)
    assert fills[0].avg_price == pytest.approx(60_000.0)
    assert fills[0].fee == pytest.approx(6.0)
    assert statuses[0].status == "filled"
    # 终态后不再跟踪
    assert adapter.sync() == []


def test_ccxt_balance_and_positions(fake_ccxt):
    adapter = CcxtAdapter(sandbox=True, api_key="k", secret="s")
    adapter.connect()
    balance = adapter.fetch_balance()
    assert balance.cash == pytest.approx(1000.0)
    assert balance.currency == "USDT"
    assert adapter.fetch_positions(["CRYPTO:BTC/USDT"]) == {}  # 假余额里没有 BTC

    prices = adapter.quote(["CRYPTO:BTC/USDT"])
    assert prices["CRYPTO:BTC/USDT"] > 0


def test_ccxt_rejects_non_crypto_symbol(fake_ccxt):
    adapter = CcxtAdapter(sandbox=True, api_key="k", secret="s")
    adapter.connect()
    with pytest.raises(AdapterError, match="CRYPTO"):
        adapter.place_order(_order(symbol="CN:600519"))


def test_ccxt_cancel(fake_ccxt):
    adapter = CcxtAdapter(sandbox=True, api_key="k", secret="s")
    adapter.connect()
    ext = adapter.place_order(_order())
    assert adapter.cancel_order(ext, "CRYPTO:BTC/USDT") is True
    assert fake_ccxt["instance"].cancelled == [(ext, "BTC/USDT")]


# --------------------------------------------------------------------------- #
# miniQMT（假 xtquant 包）
# --------------------------------------------------------------------------- #


class FakeTrader:
    def __init__(self, path, session_id):
        self.path = path
        self.session_id = session_id
        self.subscribed = None
        self.placed: list[tuple] = []
        self.cancelled: list[tuple] = []
        self.orders: list[SimpleNamespace] = []

    def start(self):
        return None

    def connect(self):
        return 0

    def subscribe(self, account):
        self.subscribed = account

    def order_stock(self, account, code, side, volume, price_type, price, strategy, remark):
        self.placed.append((code, side, volume, price_type, price))
        return 1001

    def cancel_order_stock(self, account, order_id):
        self.cancelled.append((account, order_id))
        return 0

    def query_stock_asset(self, account):
        return SimpleNamespace(cash=50_000.0, total_asset=80_000.0)

    def query_stock_positions(self, account):
        return [
            SimpleNamespace(
                stock_code="600519.SH",
                volume=100,
                can_use_volume=100,
                open_price=1500.0,
                market_value=160_000.0,
            )
        ]

    def query_stock_orders(self, account):
        return self.orders


@pytest.fixture()
def fake_xt(monkeypatch):
    holder: dict[str, FakeTrader] = {}

    class _Trader(FakeTrader):
        def __init__(self, path, session_id):
            super().__init__(path, session_id)
            holder["instance"] = self

    pkg = types.ModuleType("xtquant")
    pkg.__path__ = []  # 标记为包，允许子模块导入
    xttrader = types.ModuleType("xtquant.xttrader")
    xttrader.XtQuantTrader = _Trader
    xtconstant = types.ModuleType("xtquant.xtconstant")
    xtconstant.STOCK_BUY = 23
    xtconstant.STOCK_SELL = 24
    xtconstant.FIX_PRICE = 11
    xtconstant.LATEST_PRICE = 5
    xttype = types.ModuleType("xtquant.xttype")
    xttype.StockAccount = lambda account_id: SimpleNamespace(account_id=account_id)

    pkg.xttrader = xttrader
    pkg.xtconstant = xtconstant
    pkg.xttype = xttype
    monkeypatch.setitem(sys.modules, "xtquant", pkg)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", xttrader)
    monkeypatch.setitem(sys.modules, "xtquant.xtconstant", xtconstant)
    monkeypatch.setitem(sys.modules, "xtquant.xttype", xttype)
    return holder


def test_miniqmt_symbol_mapping():
    assert _xt_code("CN:600519") == "600519.SH"
    assert _xt_code("CN:000001") == "000001.SZ"
    assert _xt_code("CN:300059") == "300059.SZ"
    assert _xt_code("CN:830799") == "830799.BJ"
    with pytest.raises(AdapterError):
        _xt_code("US:AAPL")


def test_miniqmt_connect_requires_config():
    adapter = MiniQmtAdapter(sandbox=True, path="", account_id="")
    with pytest.raises(AdapterError, match="QMT 路径"):
        adapter.connect()


def test_miniqmt_place_cancel_and_sync(fake_xt):
    adapter = MiniQmtAdapter(sandbox=True, path="C:/QMT", account_id="123456")
    adapter.connect()
    trader = fake_xt["instance"]
    assert trader.subscribed is not None

    ext = adapter.place_order(_order(symbol="CN:600519", qty=100, limit=1500.0))
    assert ext == "1001"
    code, side, volume, price_type, price = trader.placed[0]
    assert code == "600519.SH"
    assert side == 23  # STOCK_BUY
    assert volume == 100
    assert price_type == 11  # FIX_PRICE
    assert price == pytest.approx(1500.0)

    assert adapter.cancel_order(ext, "CN:600519") is True
    assert trader.cancelled == [(trader.subscribed, 1001)]


def test_miniqmt_balance_positions_and_status(fake_xt):
    adapter = MiniQmtAdapter(sandbox=True, path="C:/QMT", account_id="123456")
    adapter.connect()
    trader = fake_xt["instance"]

    balance = adapter.fetch_balance()
    assert balance.cash == pytest.approx(50_000.0)
    assert balance.equity == pytest.approx(80_000.0)
    assert balance.currency == "CNY"

    assert adapter.fetch_positions(["CN:600519"]) == {"CN:600519": 100.0}
    detail = adapter.position_detail(["CN:600519"])
    assert detail["CN:600519"]["available"] == 100.0
    assert detail["CN:600519"]["avg_cost"] == pytest.approx(1500.0)

    # 未跟踪的委托不产生事件
    trader.orders = [
        SimpleNamespace(order_id=1001, traded_volume=100, traded_price=1510.0, order_status=56, order_time=1_700_000_000)
    ]
    adapter.place_order(_order(symbol="CN:600519", qty=100))
    events = adapter.sync()
    fills = [e for e in events if hasattr(e, "filled")]
    statuses = [e for e in events if hasattr(e, "status")]
    assert fills[0].filled == pytest.approx(100.0)
    assert fills[0].avg_price == pytest.approx(1510.0)
    assert statuses[0].status == "filled"
