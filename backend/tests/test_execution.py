"""撮合、费用、A 股交易规则与 T+1 账户。"""

from __future__ import annotations

from datetime import datetime

import pytest

from quant.core.portfolio import Account
from quant.core.types import Bar, FeeBreakdown, Fill, Order, OrderType, Side
from quant.execution.cost import CostModel
from quant.execution.matching import BlockReason, MatchingEngine


def make_bar(**overrides) -> Bar:
    defaults = {
        "symbol": "600519",
        "dt": datetime(2024, 1, 2),
        "open": 10.0,
        "high": 10.6,
        "low": 9.8,
        "close": 10.3,
        "volume": 1_000_000,
        "prev_close": 10.0,
    }
    defaults.update(overrides)
    return Bar(**defaults)


def test_cost_model_a_share_fees():
    cost = CostModel()
    buy = cost.fees(Side.BUY, 10.0, 10_000)
    assert buy.commission == pytest.approx(30.0)
    assert buy.stamp_tax == 0.0
    assert buy.transfer_fee == pytest.approx(1.0)
    assert buy.total == pytest.approx(31.0)

    sell = cost.fees(Side.SELL, 10.0, 10_000)
    assert sell.stamp_tax == pytest.approx(50.0)
    assert sell.total == pytest.approx(81.0)

    small = cost.fees(Side.BUY, 10.0, 100)
    assert small.commission == 5.0  # 最低佣金

    assert cost.max_affordable_quantity(10_000, 10.0) == 900


def test_slippage_moves_price_against_trader():
    cost = CostModel(slippage_bps=10)
    assert cost.slippage_price(Side.BUY, 10.0) == pytest.approx(10.01)
    assert cost.slippage_price(Side.SELL, 10.0) == pytest.approx(9.99)


def test_market_order_fills_at_next_open():
    engine = MatchingEngine()
    order = Order("600519", Side.BUY, 500)
    outcome = engine.match(order, make_bar(open=10.2))
    assert outcome.filled
    assert outcome.fill_price == pytest.approx(10.2)
    assert outcome.quantity == 500


def test_limit_buy_requires_price_touch():
    engine = MatchingEngine()
    order = Order("600519", Side.BUY, 500, OrderType.LIMIT, limit_price=9.5)
    outcome = engine.match(order, make_bar(low=9.8))
    assert not outcome.filled
    assert outcome.reason is BlockReason.PRICE_NOT_TOUCHED

    touched = engine.match(order, make_bar(open=9.6, low=9.4))
    assert touched.filled
    assert touched.fill_price == pytest.approx(9.5)  # min(限价, 开盘)


def test_one_word_limit_up_blocks_buy():
    engine = MatchingEngine()
    bar = make_bar(open=11.0, high=11.0, low=11.0, close=11.0, prev_close=10.0, limit_up=11.0)
    outcome = engine.match(Order("600519", Side.BUY, 100), bar)
    assert not outcome.filled
    assert outcome.reason is BlockReason.LIMIT_UP


def test_volume_participation_cap():
    engine = MatchingEngine(volume_limit_pct=0.10)
    outcome = engine.match(Order("600519", Side.BUY, 50_000), make_bar(volume=1_000))
    assert outcome.filled
    assert outcome.quantity == 100


def test_buy_quantity_rounds_to_lot():
    engine = MatchingEngine(volume_limit_pct=0.0)
    outcome = engine.match(Order("600519", Side.BUY, 250), make_bar(volume=0))
    # volume=0 会被判定为停牌；用一个有量但不受限的场景验证整手
    outcome = engine.match(Order("600519", Side.BUY, 250), make_bar())
    assert outcome.filled
    assert outcome.quantity == 200


def test_t_plus_one_available_quantity():
    account = Account(100_000)
    fill = Fill(
        order_id="o1",
        symbol="600519",
        side=Side.BUY,
        quantity=100,
        price=10.0,
        dt=datetime(2024, 1, 2, 10, 0),
        fees=FeeBreakdown(commission=5.0),
    )
    account.apply_fill(fill)
    position = account.get_position("600519")
    assert position.quantity == 100
    assert position.available == 0  # 当日买入不可卖

    account.on_new_session()
    assert position.available == 100
