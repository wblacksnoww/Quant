"""实盘券商：适配器契约、LiveBroker 状态机、回报对账与重复同步幂等。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant.core.events import EventBus
from quant.core.portfolio import Account
from quant.core.types import Bar, Order, OrderStatus, OrderType, Side, TimeInForce
from quant.execution.adapters.base import (
    AdapterBalance,
    AdapterError,
    AdapterFill,
    AdapterStatus,
    BrokerAdapter,
)
from quant.execution.live_broker import LiveBroker


class FakeAdapter(BrokerAdapter):
    """可编排的假适配器：下单返回递增的委托号，成交由测试手动推进。"""

    name = "fake"
    supports_sandbox = True

    def __init__(self, *, sandbox: bool = False, cash: float = 100_000.0, **kwargs):
        super().__init__(sandbox=sandbox, **kwargs)
        self.connected = False
        self.cash = cash
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, float] = {}
        self.placed: list[Order] = []
        self.cancelled: list[str] = []
        self._seq = 0
        self.quote_prices: dict[str, float] = {}

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def place_order(self, order: Order) -> str:
        if not self.connected:
            raise AdapterError("未连接")
        self._seq += 1
        ext = f"EXT-{self._seq}"
        self.orders[ext] = {"filled": 0.0, "avg": 0.0, "fee": 0.0, "status": "open"}
        self.placed.append(order)
        return ext

    def cancel_order(self, external_id: str, symbol: str) -> bool:
        self.cancelled.append(external_id)
        rec = self.orders.get(external_id)
        if rec is None or rec["status"] != "open":
            return False
        rec["status"] = "cancelled"
        return True

    def fetch_balance(self) -> AdapterBalance:
        return AdapterBalance(cash=self.cash, equity=self.cash + 0.0, currency="USDT")

    def fetch_positions(self, symbols):
        return {s: q for s, q in self.positions.items() if q > 0}

    def sync(self):
        events = []
        for ext, rec in list(self.orders.items()):
            if rec["filled"] > 0:
                events.append(
                    AdapterFill(
                        external_id=ext,
                        filled=rec["filled"],
                        avg_price=rec["avg"],
                        fee=rec["fee"],
                        dt=datetime(2024, 1, 2, tzinfo=UTC).replace(tzinfo=None),
                    )
                )
            if rec["status"] != "open":
                events.append(AdapterStatus(external_id=ext, status=rec["status"]))
        return events

    def quote(self, symbols):
        if self.quote_prices:
            return {s: p for s, p in self.quote_prices.items() if s in set(symbols)}
        # 默认给一个稳定报价，避免实盘引擎回退到真实行情数据源（测试要离线）
        return dict.fromkeys(symbols, 100.0)

    # 测试驱动 -------------------------------------------------------- #
    def fill(self, ext: str, filled: float, avg: float, fee: float = 0.0, status: str = "closed"):
        rec = self.orders[ext]
        rec.update(filled=filled, avg=avg, fee=fee, status=status)


def _broker(adapter: FakeAdapter, cash: float = 100_000.0) -> LiveBroker:
    account = Account(cash, name="live_test")
    return LiveBroker(account, adapter, EventBus("live_test"))


def _order(symbol="CRYPTO:BTC/USDT", side=Side.BUY, qty=0.5, limit=None, tif=TimeInForce.DAY):
    return Order(
        symbol=symbol,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET if limit is None else OrderType.LIMIT,
        limit_price=limit,
        tif=tif,
    )


# --------------------------------------------------------------------------- #
# 提交与回报
# --------------------------------------------------------------------------- #


def test_submit_records_external_id():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order())
    assert order.status is OrderStatus.SUBMITTED
    assert order.meta["external_id"] == "EXT-1"
    assert adapter.placed
    assert broker.account.frozen_cash > 0  # 买单冻结估算资金


def test_sync_applies_cumulative_fill_and_releases_freeze():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order(qty=0.5))

    adapter.fill("EXT-1", filled=0.5, avg=100.0, fee=0.05)
    fills = broker.sync()
    assert len(fills) == 1
    assert fills[0].quantity == pytest.approx(0.5)
    assert fills[0].price == pytest.approx(100.0)
    assert fills[0].fees.commission == pytest.approx(0.05)
    assert order.status is OrderStatus.FILLED
    assert broker.account.frozen_cash == pytest.approx(0.0)
    assert broker.account.get_position("CRYPTO:BTC/USDT").quantity == pytest.approx(0.5)


def test_sync_is_idempotent():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    broker.submit(_order(qty=1.0))
    adapter.fill("EXT-1", filled=1.0, avg=50.0)

    first = broker.sync()
    second = broker.sync()
    assert len(first) == 1
    assert second == []  # 累计口径已记账，不会重复
    assert len(broker.fills) == 1


def test_partial_fill_then_full_uses_incremental_prices():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order(qty=2.0))

    adapter.fill("EXT-1", filled=1.0, avg=100.0, status="open")
    broker.sync()
    assert order.status is OrderStatus.PARTIAL
    assert order.filled_quantity == pytest.approx(1.0)

    # 交易所累计：2 手、均价 110（第二手 120）
    adapter.fill("EXT-1", filled=2.0, avg=110.0, status="closed")
    fills = broker.sync()
    assert len(fills) == 1
    assert fills[0].quantity == pytest.approx(1.0)
    assert fills[0].price == pytest.approx(120.0)  # 增量价，不是累计均价
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == pytest.approx(110.0)


def test_rejected_status_marks_order():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order())
    adapter.orders["EXT-1"]["status"] = "rejected"
    broker.sync()
    assert order.status is OrderStatus.REJECTED
    assert broker.account.frozen_cash == pytest.approx(0.0)


def test_cancel_sends_to_exchange():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order(limit=1.0))
    assert broker.cancel(order.id) is True
    assert adapter.cancelled == ["EXT-1"]
    assert order.status is OrderStatus.CANCELLED


def test_end_of_day_cancels_day_orders_only():
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    day_order = broker.submit(_order(limit=1.0, tif=TimeInForce.DAY))
    gtc_order = broker.submit(_order(limit=1.0, tif=TimeInForce.GTC))

    broker.end_of_day()

    assert day_order.status is OrderStatus.CANCELLED
    assert gtc_order.status is OrderStatus.SUBMITTED
    assert "EXT-1" in adapter.cancelled
    assert "EXT-2" not in adapter.cancelled


def test_sync_delivers_pending_fills_to_session_on_bar():
    """on_bar 把上一轮 sync 的成交交给会话（策略 on_fill 依赖这条链路）。"""
    adapter = FakeAdapter()
    adapter.connect()
    broker = _broker(adapter)
    broker.submit(_order(qty=0.5))
    adapter.fill("EXT-1", filled=0.5, avg=100.0)
    broker.sync()

    bar = Bar(
        symbol="CRYPTO:BTC/USDT",
        dt=datetime(2024, 1, 2, 12, 0),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )
    pending = broker.on_bar(bar)
    assert len(pending) == 1
    assert broker.on_bar(bar) == []  # 只交付一次


# --------------------------------------------------------------------------- #
# 初始化与对账
# --------------------------------------------------------------------------- #


def test_bind_account_initializes_from_exchange():
    adapter = FakeAdapter(cash=5_000.0)
    adapter.connect()
    adapter.positions = {"CRYPTO:BTC/USDT": 0.25}
    broker = _broker(adapter, cash=1.0)

    info = broker.bind_account(["CRYPTO:BTC/USDT"], prices={"CRYPTO:BTC/USDT": 60_000.0})

    assert info["cash"] == pytest.approx(5_000.0)
    assert broker.account.cash == pytest.approx(5_000.0)
    assert broker.account.initial_cash == pytest.approx(5_000.0)
    pos = broker.account.get_position("CRYPTO:BTC/USDT")
    assert pos.quantity == pytest.approx(0.25)
    assert pos.available == pytest.approx(0.25)  # 外部持仓视为已过 T+1


def test_reconcile_reports_differences():
    adapter = FakeAdapter(cash=5_000.0)
    adapter.connect()
    broker = _broker(adapter, cash=5_000.0)
    broker.account.mark_price("CRYPTO:BTC/USDT", 100.0)
    broker.account.get_position("CRYPTO:BTC/USDT").quantity = 1.0

    adapter.cash = 4_900.0
    adapter.positions = {"CRYPTO:BTC/USDT": 0.5}
    report = broker.reconcile()

    assert report["ok"] is False
    assert report["cash_diff"] == pytest.approx(-100.0)
    assert report["positions"][0]["local"] == pytest.approx(1.0)
    assert report["positions"][0]["remote"] == pytest.approx(0.5)


def test_reconcile_clean():
    adapter = FakeAdapter(cash=1_000.0)
    adapter.connect()
    broker = _broker(adapter, cash=1_000.0)
    report = broker.reconcile()
    assert report["ok"] is True


def test_validate_rejects_insufficient_cash():
    adapter = FakeAdapter(cash=10.0)
    adapter.connect()
    broker = _broker(adapter, cash=10.0)
    order = broker.submit(_order(qty=1.0, limit=100.0))
    assert order.status is OrderStatus.REJECTED
    assert "可用资金不足" in order.reason
    assert adapter.placed == []


def test_validate_allows_fractional_crypto():
    adapter = FakeAdapter(cash=100_000.0)
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order(qty=0.000123))
    assert order.status is OrderStatus.SUBMITTED


def test_adapter_error_becomes_rejection():
    class BrokenAdapter(FakeAdapter):
        def place_order(self, order: Order) -> str:
            raise AdapterError("交易所维护中")

    adapter = BrokenAdapter()
    adapter.connect()
    broker = _broker(adapter)
    order = broker.submit(_order())
    assert order.status is OrderStatus.REJECTED
    assert "交易所维护中" in order.reason
    assert broker.account.frozen_cash == pytest.approx(0.0)
