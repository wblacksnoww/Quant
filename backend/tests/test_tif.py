"""TIF 落地：日终只过期 DAY 单，GTC 挂单保留到成交或主动撤销。"""

from __future__ import annotations

from datetime import datetime

from quant.core.events import EventBus
from quant.core.portfolio import Account
from quant.core.types import Bar, Order, OrderStatus, OrderType, Side, TimeInForce
from quant.execution.broker import SimBroker
from quant.execution.matching import MatchingEngine


def _bar() -> Bar:
    return Bar(
        symbol="CN:600519",
        dt=datetime(2024, 1, 3, 15, 0),
        open=10.0,
        high=10.5,
        low=9.8,
        close=10.2,
        volume=1_000_000.0,
    )


def _limit_buy(tif: TimeInForce) -> Order:
    return Order(
        symbol="CN:600519",
        side=Side.BUY,
        quantity=100.0,
        order_type=OrderType.LIMIT,
        limit_price=1.0,  # 远低于市价，不会被撮合
        tif=tif,
    )


def test_end_of_day_expires_only_day_orders():
    acct = Account(100_000)
    broker = SimBroker(acct, EventBus("tif_test"), matching=MatchingEngine())

    day_order = broker.submit(_limit_buy(TimeInForce.DAY))
    gtc_order = broker.submit(_limit_buy(TimeInForce.GTC))
    broker.on_bar(_bar())

    assert day_order.status is OrderStatus.SUBMITTED
    assert gtc_order.status is OrderStatus.SUBMITTED

    broker.end_of_day()

    assert day_order.status is OrderStatus.EXPIRED
    assert gtc_order.status is OrderStatus.SUBMITTED
    assert gtc_order in broker.open_orders
    assert day_order not in broker.open_orders


def test_gtc_survives_multiple_days_until_match():
    acct = Account(100_000)
    broker = SimBroker(acct, EventBus("tif_gtc"), matching=MatchingEngine())

    gtc_order = broker.submit(_limit_buy(TimeInForce.GTC))
    for _ in range(3):
        broker.on_bar(_bar())
        broker.end_of_day()
        assert gtc_order.status is OrderStatus.SUBMITTED

    # bar.high 触及限价 → 买单以 min(限价, 开盘价) 成交
    hit = Bar(
        symbol="CN:600519",
        dt=datetime(2024, 1, 8, 15, 0),
        open=0.8,
        high=1.2,
        low=0.7,
        close=0.9,
        volume=1_000_000.0,
    )
    broker.on_bar(hit)
    assert gtc_order.status is OrderStatus.FILLED
